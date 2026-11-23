import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from fixtures.generate_v1 import generate
from whoanswered.fixtures import FRAME_BYTES, FRAME_MS, load_pack
from whoanswered.policy import InMemoryPlaybackSink, PolicyEvent, run_policy
from whoanswered.replay import Observation, replay


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PACK = FIXTURES / "v1"
FAMILIES = {
    "clean-pickup": (4, {"human"}),
    "short-mailbox": (4, {"voicemail"}),
    "paused-mailbox": (4, {"voicemail"}),
    "connected-silence": (3, {"silence"}),
    "media-loss": (3, {"unknown"}),
    "confirmed-disconnect": (3, {"disconnect"}),
    "screener-to-human": (2, {"human"}),
    "screener-to-mailbox": (2, {"voicemail"}),
    "screener-to-disconnect": (2, {"disconnect"}),
    "matched-hello": (2, {"human", "voicemail"}),
}
SOURCES = {
    "pickup-hello": (10601, 2),
    "pickup-yes": (10602, 2),
    "mailbox-note": (10603, 2),
    "mailbox-unavailable": (10604, 2),
    "mailbox-away": (10605, 2),
    "mailbox-later": (10606, 2),
    "connected-silence": (10607, 3),
    "media-loss": (10608, 3),
    "confirmed-disconnect": (10609, 3),
    "simulated-screener-name": (10610, 3),
    "simulated-screener-caller": (10611, 3),
    "matched-hello": (10612, 2),
}


class ChallengePackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_pack(PACK)
        cls.by_id = {case.case_id: case for case in cls.cases}
        manifest = json.loads((PACK / "manifest.json").read_bytes())
        cls.entries = {entry["id"]: entry for entry in manifest["cases"]}

    def test_asset_hashes_and_regeneration(self):
        generated_paths = {Path("manifest.json")}
        for case_id, entry in self.entries.items():
            for filename, payload in entry["payloads"].items():
                with self.subTest(case=case_id, payload=filename):
                    relative_path = Path(case_id) / filename
                    self.assertEqual(payload["path"], relative_path.as_posix())
                    self.assertEqual(
                        hashlib.sha256((PACK / relative_path).read_bytes()).hexdigest(),
                        payload["sha256"],
                    )
                    generated_paths.add(relative_path)

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            generate(out)
            self.assertEqual(
                {path.relative_to(out) for path in out.rglob("*") if path.is_file()},
                generated_paths,
            )
            for relative_path in sorted(generated_paths):
                with self.subTest(regenerated=relative_path):
                    self.assertEqual(
                        (out / relative_path).read_bytes(), (PACK / relative_path).read_bytes()
                    )

    def test_required_case_families(self):
        counts = Counter(entry["family"] for entry in self.entries.values())
        self.assertEqual(counts, {family: count for family, (count, _) in FAMILIES.items()})
        for family, (_, truths) in FAMILIES.items():
            with self.subTest(family=family):
                cases = [case for case in self.cases if self.entries[case.case_id]["family"] == family]
                self.assertEqual({case.expected.answerer for case in cases}, truths)
                for case in cases:
                    events = case.observable.events
                    kinds = {event.event for event in events}
                    if family == "connected-silence":
                        self.assertTrue({"connected", "start", "media"} <= kinds)
                        self.assertTrue({"stop", "call_completed"}.isdisjoint(kinds))
                    elif family == "media-loss":
                        self.assertIn("start", kinds)
                        self.assertNotIn("call_completed", kinds)
                        media_count = sum(event.event == "media" for event in events)
                        frame_count = len(case.observable.audio) // FRAME_BYTES
                        self.assertTrue(
                            "stop" in kinds or media_count == 0 or media_count < frame_count
                        )
                    elif family == "confirmed-disconnect":
                        self.assertIn("call_completed", kinds)

    def test_source_groups_and_license_declarations(self):
        counts = Counter(case.source_group for case in self.cases)
        self.assertEqual(counts, {group: count for group, (_, count) in SOURCES.items()})
        for case in self.cases:
            with self.subTest(case=case.case_id):
                self.assertEqual(self.entries[case.case_id]["seed"], SOURCES[case.source_group][0])
                self.assertEqual(case.license, "MIT")
                self.assertIn("First-party synthetic audio and scripted events.", case.provenance)

        original = self.by_id["clean-pickup-hello"].observable.audio
        delayed = self.by_id["clean-pickup-hello-delayed"].observable.audio
        delay_bytes = 420 * FRAME_BYTES // FRAME_MS
        self.assertEqual(original[:-delay_bytes], delayed[delay_bytes:])
        self.assertEqual(delayed[:delay_bytes], b"\xff" * delay_bytes)

        grant = (FIXTURES / "LICENSE.md").read_text(encoding="utf-8")
        self.assertIn("MIT License", grant)
        self.assertIn("first-party generated fixture content", grant)
        self.assertIn("Permission is hereby granted", grant)

    def test_generic_screeners_are_simulated_and_drive_policy_actions(self):
        prompt_prefixes = {}
        transitions = {
            "screener-to-human": "human_takeover",
            "screener-to-mailbox": "mailbox_ready",
            "screener-to-disconnect": "disconnect",
        }
        for case in self.cases:
            entry = self.entries[case.case_id]
            with self.subTest(case=case.case_id):
                if entry["family"] not in transitions:
                    self.assertEqual(case.track, "acoustic")
                    self.assertNotIn("policy_events", entry)
                    continue

                self.assertEqual(case.track, "policy")
                self.assertTrue(case.case_id.startswith("simulated-screener-"))
                self.assertIs(entry["simulated"], True)
                self.assertTrue(case.provenance.startswith("Simulated generic screener"))
                events = [PolicyEvent(**event) for event in entry["policy_events"]]
                self.assertEqual(
                    [event.event for event in events],
                    ["screener", "prompt_end", transitions[entry["family"]]],
                )
                self.assertLess(events[0].at_ms, events[1].at_ms)
                self.assertLess(events[1].at_ms, events[2].at_ms)
                last_prompt_byte = events[1].at_ms * FRAME_BYTES // FRAME_MS
                prefix = case.observable.audio[:last_prompt_byte]
                self.assertEqual(prefix, prompt_prefixes.setdefault(case.source_group, prefix))
                completed = [
                    event.arrival_ms for event in case.observable.events
                    if event.event == "call_completed"
                ]
                self.assertEqual(completed, [events[2].at_ms] if events[2].event == "disconnect" else [])

                sink = InMemoryPlaybackSink()
                run_policy(events, sink)
                self.assertEqual(
                    [(action.action, action.at_ms) for action in sink.trace],
                    [(action.action, action.at_ms) for action in case.expected.actions],
                )

    def test_matched_hello_has_identical_detector_prefix_through_2000_ms(self):
        pair = [case for case in self.cases if case.expected.matched_pair is not None]
        self.assertEqual(
            [case.case_id for case in pair], ["matched-hello-human", "matched-hello-mailbox"]
        )
        self.assertEqual({case.expected.matched_pair for case in pair}, {"hello-2000"})
        self.assertEqual({case.source_group for case in pair}, {"matched-hello"})
        self.assertEqual([case.expected.answerer for case in pair], ["human", "voicemail"])
        self.assertEqual({case.expected.verdict for case in pair}, {"uncertain"})

        captures = []

        def detector_factory():
            stream = []
            captures.append(stream)
            return stream.append

        with tempfile.TemporaryDirectory() as directory:
            replay(PACK, Path(directory), detector_factory=detector_factory)

        streams = dict(zip(self.by_id, captures, strict=True))
        prefixes = []
        tails = []
        for case in pair:
            stream = streams[case.case_id]
            cutoff = stream.index(Observation("cutoff", 2_000))
            prefixes.append(stream[:cutoff + 1])
            tails.append({
                obs.sample_start_ms: obs.audio
                for obs in stream[cutoff + 1:]
                if obs.event == "media"
            })

        # Observation equality covers raw frame bytes and all signaling, times, and sequences.
        self.assertEqual(prefixes[0], prefixes[1])
        prefix = prefixes[0]
        self.assertEqual([obs.event for obs in prefix[:3]], ["call_answered", "connected", "start"])
        frames = [obs for obs in prefix if obs.event == "media"]
        self.assertEqual([obs.sample_start_ms for obs in frames], list(range(0, 2_000, FRAME_MS)))
        self.assertEqual(frames[-1].at_ms, 2_000)
        self.assertTrue(any(obs.audio != b"\xff" * FRAME_BYTES for obs in frames))
        shared_tail_times = tails[0].keys() & tails[1].keys()
        self.assertTrue(any(tails[0][at_ms] != tails[1][at_ms] for at_ms in shared_tail_times))


if __name__ == "__main__":
    unittest.main()
