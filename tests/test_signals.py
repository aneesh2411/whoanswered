import json
import tempfile
import unittest
from hashlib import sha256
from math import pi, sin
from pathlib import Path

from fixtures.generate_v1 import mulaw
from fixtures.generate_v3 import generate
from whoanswered import (
    FusionDetector, ToneDetector, TranscriptCueDetector, V1Detector, evaluate, replay_pack,
)
from whoanswered.comparison import _signal_detector_configs
from whoanswered.fixtures import (
    FRAME_BYTES, FRAME_MS, SILENT_FRAME, FixtureValidationError, _read_events, canonical_json,
    canonical_jsonl, load_pack, validate_split_boundary,
)


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "fixtures" / "v3"
MANIFEST_DIGESTS = {
    "dev": "d12c560e21287e4435e4f731ee45c5d2aef0793060a9173dc29731a30ae735e9",
    "eval": "6180a70b654ff20338026364f8bea64e1dfabab60cedfcd881e67860e6d08544",
}
SPEECH = b"\x00" * FRAME_BYTES


def beep(frequencies, duration_ms):
    samples = bytes(
        mulaw(round(sum(4800 * sin(2 * pi * hz * i / 8000) for hz in frequencies)))
        for i in range(8 * duration_ms)
    )
    return [samples[i : i + FRAME_BYTES] for i in range(0, len(samples), FRAME_BYTES)]


def feed(session, frames, start_ms=0):
    for i, frame in enumerate(frames, 1):
        session.feed_audio(frame, t_ms=start_ms + i * FRAME_MS)


class TranscriptTest(unittest.TestCase):
    def test_phrase_categories_and_immutable_commitment(self):
        for text, expected in (
            ("Please LEAVE a message.", "voicemail"),
            ("Speak AFTER THE TONE.", "voicemail"),
            ("I am not available.", "voicemail"),
            ("Start at the beep.", "voicemail"),
            ("This is the voice mailbox.", "voicemail"),
            ("Record your name.", "screener"),
            ("Please state your name and reason for calling.", "screener"),
            ("This call is being screened", "screener"),
            ("Hello, who is calling", "human"),
            ("Hey, ready?", "human"),
            ("Hi, this is Priya.", "human"),
            ("Hello, Priya.", "human"),
        ):
            with self.subTest(text=text):
                session = TranscriptCueDetector().start()
                self.assertIsNone(session.feed_transcript(text, t_ms=300, final=False))
                self.assertFalse(session.decision().committed)
                decision = session.feed_transcript(text, t_ms=540, final=True)
                self.assertEqual((decision.label, decision.at_ms, decision.committed), (expected, 540, True))
                self.assertIsNone(session.feed_transcript("Leave a message", t_ms=740, final=True))
                self.assertIs(session.decision(), decision)

    def test_unclear_empty_and_mixed_cues(self):
        session = TranscriptCueDetector().start()
        for text in ("Hello?", "", "It is not availableish.", "Hi there.",
                     "Record your name or leave a message."):
            with self.subTest(text=text):
                self.assertIsNone(session.feed_transcript(text, t_ms=500, final=True))
        self.assertEqual(session.decision().label, "uncertain")
        for session in (V1Detector().start(), ToneDetector().start()):
            self.assertIsNone(session.feed_transcript("Leave a message", t_ms=1, final=True))


class ToneTest(unittest.TestCase):
    def test_beep_after_speech_commits_at_observed_end(self):
        for hz, duration in ((400, 160), (437, 200), (1337, 300), (2000, 600)):
            with self.subTest(hz=hz, duration=duration):
                session = ToneDetector().start()
                feed(session, [SPEECH] * 6 + beep((hz,), duration))
                self.assertEqual(session.decision().label, "uncertain")
                decision = session.feed_audio(SILENT_FRAME, t_ms=140 + duration)
                self.assertEqual((decision.label, decision.at_ms), ("voicemail", 140 + duration))

    def test_controls_abstain(self):
        for speech, frequencies, duration in (
            ([], (1000,), 200),
            ([SPEECH], (1000,), 200),
            ([SPEECH] * 6, (1000,), 100),
            ([SPEECH] * 6, (1000,), 640),
            ([SPEECH] * 6, (250,), 200),
            ([SPEECH] * 6, (2500,), 200),
            ([SPEECH] * 6, (700, 1200), 240),
        ):
            with self.subTest(frequencies=frequencies, duration=duration, speech_frames=len(speech)):
                session = ToneDetector().start()
                feed(session, speech + beep(frequencies, duration) + [SILENT_FRAME])
                self.assertEqual(session.decision().label, "uncertain")
        session = ToneDetector().start()
        feed(session, [SPEECH] * 6 + beep((1000,), 160))
        session.feed_audio(SILENT_FRAME, t_ms=320)  # The end falls after a missing frame.
        self.assertEqual(session.decision().label, "uncertain")


class FusionTest(unittest.TestCase):
    def test_audio_agreement_and_mailbox_veto(self):
        detector = FusionDetector(V1Detector(), TranscriptCueDetector(), ToneDetector())
        human, mailbox, conflict, audio_only = (detector.start() for _ in range(4))
        for session in (human, mailbox, conflict, audio_only):
            feed(session, [SPEECH] * 6 + [SILENT_FRAME] * 24)
        for session in (human, mailbox):
            self.assertIsNone(session.feed_transcript("Hi, who is calling?", t_ms=610, final=True))
        self.assertIsNone(conflict.feed_transcript("After the tone", t_ms=630, final=True))
        feed(mailbox, beep((960,), 200) + [SILENT_FRAME], start_ms=1000)
        self.assertEqual(mailbox.decision().label, "voicemail")
        self.assertEqual(mailbox.decision().at_ms, 1220)
        self.assertEqual(human.feed_event("cutoff", t_ms=2000).label, "human")
        self.assertEqual(conflict.feed_event("cutoff", t_ms=2000).reason, "conflicting_signals")
        self.assertEqual(audio_only.feed_event("cutoff", t_ms=2000).label, "uncertain")
        self.assertIsNone(audio_only.feed_transcript("Record your name", t_ms=2010, final=True))
        self.assertEqual(audio_only.decision().label, "uncertain")

    def test_mailbox_agreement_and_decisive_screener(self):
        mailbox, screener = (FusionDetector().start() for _ in range(2))
        mailbox.feed_transcript("Leave a message.", t_ms=10, final=True)
        self.assertFalse(mailbox.decision().committed)
        feed(mailbox, [SPEECH] * 50)
        self.assertEqual(mailbox.decision().label, "voicemail")
        self.assertEqual(mailbox.decision().reason, "audio_transcript_agreement")
        self.assertEqual(screener.feed_transcript("Record your name.", t_ms=840, final=True).label, "screener")
        self.assertIsNone(screener.feed_event("call_completed", t_ms=850))
        self.assertEqual(screener.decision().label, "screener")


class SignalPackTest(unittest.TestCase):
    def test_event_payload_validation(self):
        answer = {"event": "call_answered", "arrival_ms": 0, "sequence": 1}
        event = {"event": "transcript", "arrival_ms": 610, "sequence": 2, "text": "", "final": False}
        data = canonical_jsonl([answer, event])
        parsed = _read_events(data, 0, "events.jsonl", allow_transcript=True)
        self.assertEqual((parsed[1].text, parsed[1].final, parsed[1].arrival_ms), ("", False, 610))
        with self.assertRaisesRegex(FixtureValidationError, "INVALID_EVENT"):
            _read_events(data, 0, "events.jsonl")
        for fields in ({"text": None}, {"final": 1}, {"media": {}}, {"arrival_ms": True}):
            with self.subTest(fields=fields), self.assertRaises(FixtureValidationError):
                _read_events(canonical_jsonl([answer, event | fields]), 0, "events.jsonl", allow_transcript=True)

    def test_frozen_packs_regenerate_byte_for_byte(self):
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        generate(out)
        all_cases = []
        for split, digest in MANIFEST_DIGESTS.items():
            with self.subTest(split=split):
                cases = load_pack(PACK / split)
                self.assertEqual(len(cases), 20)
                self.assertTrue(all(case.split == split and case.pack_version == 3 for case in cases))
                all_cases.extend(cases)
                self.assertEqual(sha256((PACK / split / "manifest.json").read_bytes()).hexdigest(), digest)
                for source in (PACK / split).rglob("*"):
                    if source.is_file():
                        self.assertEqual(source.read_bytes(), (out / split / source.relative_to(PACK / split)).read_bytes())
        validate_split_boundary(all_cases)

    def test_replay_records_transcript_and_respects_cutoff(self):
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        card = replay_pack(FusionDetector(), PACK / "dev", out)
        self.assertEqual(len(card["matched_prefix_assertions"]), 2)
        self.assertTrue(all(pair["passed"] for pair in card["matched_prefix_assertions"]))

        for case_id, time, verdict, before in (
            ("dev-human-at-cutoff", 2000, "human", True),
            ("dev-mailbox-late-final", 2001, "uncertain", False),
        ):
            with self.subTest(case_id=case_id):
                trace = json.loads((out / case_id / "trace.json").read_bytes())
                cutoff = next(i for i, obs in enumerate(trace) if obs["event"] == "cutoff")
                final = next(i for i, obs in enumerate(trace) if obs.get("final"))
                self.assertEqual(trace[final]["at_ms"], time)
                self.assertIn("text", trace[final])
                self.assertEqual(final < cutoff, before)
                self.assertEqual(trace[cutoff]["verdict"], verdict)
        self.assertTrue(all(pair["passed"] for pair in evaluate(V1Detector(), PACK / "dev")["matched_prefix_assertions"]))

    def test_published_report_is_canonical_and_pins_eval(self):
        raw = (ROOT / "docs" / "v3-eval-comparison.json").read_bytes()
        report = json.loads(raw)
        self.assertEqual(raw, canonical_json(report))
        self.assertEqual(report["pack_manifest_sha256"], MANIFEST_DIGESTS["eval"])
        self.assertEqual(report["detectors"], _signal_detector_configs())
        self.assertEqual(set(report["acoustic"]), {"v1", "tone", "transcript", "fusion"})
        self.assertEqual(report["signal_provenance"], {
            "transcripts_and_audio": "authored synthetic",
            "real_asr_output_tested": False,
            "real_phone_audio_tested": False,
        })
