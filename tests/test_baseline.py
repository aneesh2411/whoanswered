import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from whoanswered.baseline import AudioBaseline, DetectorUpdate, decode_mulaw
from whoanswered.fixtures import FRAME_BYTES, FRAME_MS, load_pack
from whoanswered.replay import Observation, replay


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "fixtures" / "baseline"
ACTIVE = b"\x80\x00" * (FRAME_BYTES // 2)
QUIET = b"\xff" * FRAME_BYTES


class BaselineTest(unittest.TestCase):
    def setUp(self):
        self.out = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_mulaw_decode(self):
        for sample, pcm in (
            (0xFF, 0), (0x7F, 0), (0xFE, 8), (0x7E, -8), (0xEF, 132),
            (0x6F, -132), (0x80, 32124), (0x00, -32124),
        ):
            with self.subTest(sample=sample):
                self.assertEqual(decode_mulaw(sample), pcm)

    def test_pinned_pack_commits_on_clear_cases_and_abstains_on_other_cases(self):
        replay(PACK, self.out)
        expected = {
            "ambiguous-greeting": ("uncertain", 2000, "cutoff", "ambiguous_greeting"),
            "clear-human": ("human", 600, "media", "short_greeting_then_pause"),
            "clear-voicemail": ("voicemail", 1000, "media", "long_greeting"),
            "confirmed-disconnect": ("uncertain", 2000, "cutoff", "confirmed_disconnect"),
            "missing-media": ("uncertain", 2000, "cutoff", "missing_media"),
        }
        cases = load_pack(PACK)
        self.assertEqual({case.case_id for case in cases}, expected.keys())
        for case in cases:
            with self.subTest(case=case.case_id):
                case_out = self.out / case.case_id
                result = json.loads((case_out / "result.json").read_bytes())
                trace = json.loads((case_out / "trace.json").read_bytes())
                verdict, at_ms, event, reason = expected[case.case_id]
                self.assertEqual(case.expected.verdict, verdict)
                self.assertEqual(result, {
                    "decision": verdict, "at_ms": 2000, "provisional": True,
                })
                self.assertEqual([
                    (entry["verdict"], entry["at_ms"], entry["event"], entry["reason"])
                    for entry in trace if "verdict" in entry
                ], [(verdict, at_ms, event, reason)])

    def test_transport_observations_have_distinct_trace_reasons(self):
        replay(PACK, self.out)
        trace_path = self.out / "confirmed-disconnect" / "trace.json"
        trace = json.loads(trace_path.read_bytes())
        self.assertEqual([
            (entry["event"], entry["reason"]) for entry in trace if "reason" in entry
        ], [
            ("media", "connected_silence"),
            ("stop", "stream_loss"),
            ("call_completed", "confirmed_disconnect"),
            ("cutoff", "confirmed_disconnect"),
        ])

    def test_duplicate_frames_and_gaps_add_no_duration(self):
        detector = AudioBaseline()
        for sequence in range(1, 51):
            self.assertIsNone(detector(Observation("media", 20, sequence, 0, ACTIVE)))
        for index in range(1, 6):
            sample_ms = index * FRAME_MS
            self.assertIsNone(detector(Observation(
                "media", sample_ms + FRAME_MS, sample_start_ms=sample_ms, audio=ACTIVE,
            )))
        self.assertEqual(
            detector(Observation("media", 1020, sample_start_ms=1000, audio=QUIET)),
            DetectorUpdate("connected_silence"),
        )
        self.assertEqual(
            detector(Observation("cutoff", 2000)),
            DetectorUpdate("insufficient_evidence", "uncertain"),
        )

    def test_out_of_order_media_counts_by_sample_time(self):
        detector = AudioBaseline()
        for index in [0, *range(2, 30)]:
            sample_ms = index * FRAME_MS
            update = detector(Observation(
                "media", sample_ms + FRAME_MS, sample_start_ms=sample_ms,
                audio=ACTIVE if index < 6 else QUIET,
            ))
            self.assertIsNone(update.verdict if update is not None else None)
        self.assertEqual(
            detector(Observation("media", 620, sample_start_ms=20, audio=ACTIVE)),
            DetectorUpdate("short_greeting_then_pause", "human"),
        )

    def test_smoke_replay_produces_byte_identical_outputs(self):
        first = self.out / "first"
        second = self.out / "second"
        for out in (first, second):
            subprocess.run([
                sys.executable, "-m", "whoanswered", "replay", "--pack", "fixtures/smoke",
                "--out", out,
            ], cwd=ROOT, check=True)
        for filename in ("result.json", "trace.json"):
            with self.subTest(filename=filename):
                self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes())


if __name__ == "__main__":
    unittest.main()
