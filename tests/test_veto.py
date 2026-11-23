import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from whoanswered.baseline import DetectorUpdate
from whoanswered.fixtures import FRAME_BYTES
from whoanswered.replay import Observation
from whoanswered.veto import CutoffVeto


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = b"\x80\x00" * (FRAME_BYTES // 2)
QUIET = b"\xff" * FRAME_BYTES


class CutoffVetoTest(unittest.TestCase):
    def test_pinned_replays(self):
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for pack, expected in (
            ("v2/dev", {
                "dev-resumed-human-near": ("uncertain", "resumed_speech"),
                "dev-resumed-human-after": ("human", "short_greeting_then_pause"),
                "dev-resumed-human-after-media-gap": ("uncertain", "insufficient_evidence"),
                "dev-paused-mailbox-near": ("uncertain", "resumed_speech"),
                "dev-matched-prefix-human": ("uncertain", "resumed_speech"),
                "dev-matched-prefix-mailbox": ("uncertain", "resumed_speech"),
            }),
            ("v1", {
                "clean-pickup-hello-delayed": ("human", "short_greeting_then_pause"),
                "connected-silence-digital": ("uncertain", "connected_silence"),
                "media-loss-no-frames": ("uncertain", "insufficient_evidence"),
            }),
        ):
            subprocess.run([
                sys.executable, "-m", "whoanswered", "replay", "--detector", "veto",
                "--pack", f"fixtures/{pack}", "--out", out / pack,
            ], cwd=ROOT, check=True)
            for case_id, (verdict, reason) in expected.items():
                with self.subTest(case_id=case_id):
                    case_out = out / pack / case_id
                    trace = json.loads((case_out / "trace.json").read_bytes())
                    self.assertEqual([entry for entry in trace if "verdict" in entry], [{
                        "event": "cutoff", "at_ms": 2000, "verdict": verdict, "reason": reason,
                    }])
                    self.assertEqual(json.loads((case_out / "result.json").read_bytes()), {
                        "at_ms": 2000, "decision": verdict, "provisional": True,
                    })

        left, right = (out / "v2/dev" / f"dev-matched-prefix-{side}" for side in ("human", "mailbox"))
        self.assertEqual((left / "result.json").read_bytes(), (right / "result.json").read_bytes())
        traces = [json.loads((case / "trace.json").read_bytes()) for case in (left, right)]
        cutoff = next(i for i, entry in enumerate(traces[0]) if entry["event"] == "cutoff")
        self.assertEqual(traces[0][:cutoff + 1], traces[1][:cutoff + 1])
        self.assertNotEqual(traces[0][cutoff + 1:], traces[1][cutoff + 1:])

    def test_last_frame_can_veto_but_later_arrivals_cannot_change_decision(self):
        for last_frame, expected in (
            (ACTIVE, DetectorUpdate("resumed_speech", "uncertain")),
            (QUIET, DetectorUpdate("short_greeting_then_pause", "human")),
            (None, DetectorUpdate("insufficient_evidence", "uncertain")),
        ):
            with self.subTest(expected=expected):
                detector = CutoffVeto()
                for start_ms in range(0, 1980, 20):
                    audio = ACTIVE if start_ms < 120 else QUIET
                    self.assertIsNone(detector(Observation(
                        "media", start_ms + 20, sample_start_ms=start_ms, audio=audio,
                    )))
                if last_frame is not None:
                    self.assertIsNone(detector(Observation(
                        "media", 2000, sample_start_ms=1980, audio=last_frame,
                    )))
                self.assertEqual(detector(Observation("cutoff", 2000)), expected)
                self.assertIsNone(detector(Observation(
                    "media", 2001, sample_start_ms=1980, audio=ACTIVE,
                )))
                self.assertIsNone(detector(Observation(
                    "media", 2020, sample_start_ms=2000, audio=ACTIVE,
                )))
