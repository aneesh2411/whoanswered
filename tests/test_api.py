import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from whoanswered import Decision, V1Detector, evaluate, replay_pack
from whoanswered.fixtures import canonical_json


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
EXPECTED = ROOT / "tests" / "expected" / "v1"
PLUGIN = "examples.always_uncertain:create_detector"


class StreamingTest(unittest.TestCase):
    def test_v1_sessions_are_independent_and_commit_once(self):
        detector = V1Detector()
        human, voicemail, quiet = (detector.start() for _ in range(3))
        self.assertEqual(human.decision().label, "uncertain")
        self.assertFalse(human.decision().committed)
        self.assertIsNone(human.feed_event("playback_started", t_ms=0))

        speech, silence = b"\x00" * 160, b"\xff" * 160
        committed = Decision("human", 600, True, "short_greeting_then_pause")
        for index in range(1, 51):
            human_update = human.feed_audio(speech if index <= 6 else silence, t_ms=index * 20)
            voicemail_update = voicemail.feed_audio(speech, t_ms=index * 20)
            self.assertEqual(human_update, committed if index == 30 else None)

        self.assertEqual(voicemail_update, Decision("voicemail", 1000, True, "long_greeting"))
        self.assertIsNone(human.feed_event("call_completed", t_ms=1020))
        self.assertEqual(human.decision(), committed)
        with self.assertRaises(FrozenInstanceError):
            human.decision().label = "voicemail"
        self.assertEqual(quiet.feed_event("cutoff", t_ms=2000), Decision("uncertain", 2000, False, "missing_media"))

    def test_v1_api_reproduces_frozen_scorecard_and_traces(self):
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pack = FIXTURES / "v1"
        detector = V1Detector()
        scorecard_bytes = (EXPECTED / "scorecard.json").read_bytes()
        self.assertEqual(canonical_json(evaluate(detector, pack)), scorecard_bytes)
        self.assertEqual(canonical_json(replay_pack(detector, str(pack), str(out))), scorecard_bytes)
        for name in ("scorecard.json", "traces.jsonl"):
            self.assertEqual((out / name).read_bytes(), (EXPECTED / name).read_bytes())


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_cli(self, command, pack, spec):
        return subprocess.run(
            [sys.executable, "-m", "whoanswered", command, "--pack", FIXTURES / pack,
             "--out", self.temp / command, "--detector", spec],
            cwd=ROOT,
            capture_output=True,
        )

    def test_example_plugin_replays_and_compares(self):
        replay = self.run_cli("replay", "v1", PLUGIN)
        self.assertEqual((replay.returncode, replay.stdout, replay.stderr), (0, b"", b""))
        traces = (self.temp / "replay" / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        for line in traces:
            record = json.loads(line)
            self.assertEqual(record["initial_decision"]["decision"], "uncertain")
            cutoff = next(observation for observation in record["observations"] if observation["event"] == "cutoff")
            self.assertEqual(cutoff["reason"], "always_uncertain")

        compare = self.run_cli("compare", "v2/eval", PLUGIN)
        self.assertEqual((compare.returncode, compare.stdout, compare.stderr), (0, b"", b""))
        report = json.loads((self.temp / "compare" / "comparison.json").read_bytes())
        self.assertEqual(set(report["acoustic"]), {"v1", PLUGIN})
        self.assertEqual(report["acoustic"][PLUGIN]["committed_coverage"]["numerator"], 0)
        self.assertFalse(report["promotion"][PLUGIN])

    def test_invalid_specs_fail_without_writing_output(self):
        for spec, message in (
            ("unknown", "unknown detector 'unknown' (choose from v1, veto, score)"),
            ("examples.always_uncertain:", "invalid detector spec 'examples.always_uncertain:' (expected module:attribute)"),
            ("no_such_detector_module:create", "cannot import detector module 'no_such_detector_module': No module named 'no_such_detector_module'"),
            ("examples.always_uncertain:missing", "detector factory 'examples.always_uncertain:missing' is missing or not callable"),
            ("examples.always_uncertain:_Session", "detector factory 'examples.always_uncertain:_Session' must return a Detector with start()"),
        ):
            with self.subTest(spec=spec):
                result = self.run_cli("replay", "smoke", spec)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, f"whoanswered: {message}\n".encode())
                self.assertFalse((self.temp / "replay").exists())
