import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from whoanswered.fixtures import canonical_json
from whoanswered.replay import replay


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "fixtures" / "v1"
EXPECTED = ROOT / "tests" / "expected" / "v1"
REPLAY_ARGS = (sys.executable, "-m", "whoanswered", "replay", "--pack", "fixtures/v1")


class ScorecardTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.out = self.temp / "out"

    def read_scorecard(self):
        return json.loads((self.out / "scorecard.json").read_bytes())

    def read_traces(self):
        records = (self.out / "traces.jsonl").read_bytes().splitlines()
        return {record["case_id"]: record for record in map(json.loads, records)}

    def copy_pack(self):
        pack = self.temp / "pack"
        shutil.copytree(PACK, pack)
        manifest = pack / "manifest.json"
        return pack, manifest, json.loads(manifest.read_bytes())

    def assert_rate(self, metric, numerator, denominator, rate):
        self.assertEqual(metric, {
            "numerator": numerator, "denominator": denominator, "rate": rate,
        })

    def test_reference_counts_and_assertions(self):
        replay(PACK, self.out)
        scorecard = self.read_scorecard()
        acoustic = scorecard["acoustic"]
        self.assert_rate(acoustic["committed_coverage"], 7, 23, 7 / 23)
        self.assert_rate(acoustic["committed_error"], 3, 7, 3 / 7)
        self.assert_rate(scorecard["wrong_voicemail_routing"], 0, 7, 0.0)
        verdicts = acoustic["verdict_assertions"]
        self.assertEqual((verdicts["passed"], verdicts["total"]), (20, 23))
        self.assertEqual([failure["case_id"] for failure in verdicts["failures"]], [
            "paused-mailbox-away", "paused-mailbox-away-long-gap", "paused-mailbox-later-long-gap",
        ])
        self.assertEqual(scorecard["matched_prefix_assertions"], [{
            "matched_pair": "hello-2000",
            "identical_prefix": True,
            "initial_verdicts": {
                "matched-hello-human": "uncertain", "matched-hello-mailbox": "uncertain",
            },
            "passed": True,
        }])
        policy = scorecard["policy_assertions"]
        self.assertEqual((policy["passed"], policy["total"]), (6, 6))

    def test_late_policy_actions_keep_the_initial_verdict(self):
        replay(PACK, self.out)
        traces = self.read_traces()
        expected_actions = {
            "disconnect": "cancel_playback",
            "human": "start_conversation",
            "mailbox": "play_voicemail",
        }
        for outcome, action in expected_actions.items():
            case_id = f"simulated-screener-caller-to-{outcome}"
            with self.subTest(case_id=case_id):
                trace = traces[case_id]
                self.assertEqual(trace["initial_decision"], {
                    "at_ms": 2000, "decision": "uncertain", "provisional": True,
                })
                self.assertEqual(trace["actions"], [
                    {"action": "reply_identity_and_purpose", "at_ms": 1020},
                    {"action": action, "at_ms": 2420},
                ])

    def test_v1_paused_mailbox_false_human_commitment_timing(self):
        subprocess.run([*REPLAY_ARGS, "--out", self.out, "--detector", "v1"], cwd=ROOT, check=True)
        traces = self.read_traces()
        for case_id in (
            "paused-mailbox-away", "paused-mailbox-away-long-gap", "paused-mailbox-later-long-gap",
        ):
            with self.subTest(case_id=case_id):
                trace = traces[case_id]
                verdicts = [
                    (observation["at_ms"], observation["verdict"], observation["reason"])
                    for observation in trace["observations"] if "verdict" in observation
                ]
                self.assertEqual(verdicts, [(740, "human", "short_greeting_then_pause")])
                self.assertEqual(trace["initial_decision"], {
                    "at_ms": 2000, "decision": "human", "provisional": True,
                })

    def test_forced_guess_fails_matched_prefix_assertion(self):
        def guess(observation):
            if observation.event == "cutoff":
                return "human"
            return None

        replay(PACK, self.out, detector_factory=lambda: guess)
        scorecard = self.read_scorecard()
        self.assert_rate(scorecard["acoustic"]["committed_coverage"], 23, 23, 1.0)
        self.assert_rate(scorecard["acoustic"]["committed_error"], 18, 23, 18 / 23)
        self.assertEqual(scorecard["matched_prefix_assertions"], [{
            "matched_pair": "hello-2000",
            "identical_prefix": True,
            "initial_verdicts": {
                "matched-hello-human": "human", "matched-hello-mailbox": "human",
            },
            "passed": False,
        }])

    def test_always_abstain_control_has_no_coverage_and_fails_positive_actions(self):
        replay(
            PACK, self.out,
            detector_factory=lambda: lambda observation: None,
            policy_runner=lambda events, sink: None,
        )
        scorecard = self.read_scorecard()
        self.assert_rate(scorecard["acoustic"]["committed_coverage"], 0, 23, 0.0)
        self.assert_rate(scorecard["acoustic"]["committed_error"], 0, 0, None)
        self.assertTrue(scorecard["matched_prefix_assertions"][0]["passed"])
        policy = scorecard["policy_assertions"]
        self.assertEqual((policy["passed"], policy["total"]), (0, 6))
        for case in policy["cases"]:
            with self.subTest(case_id=case["case_id"]):
                self.assertEqual(len(case["expected_actions"]), 2)
                self.assertEqual(case["actual_actions"], [])
                self.assertFalse(case["passed"])

    def test_injected_policy_events_give_no_acoustic_credit(self):
        pack, manifest, entries = self.copy_pack()
        entries["cases"] = [case for case in entries["cases"] if case["track"] == "policy"]
        manifest.write_bytes(canonical_json(entries))
        seen = set()

        def commit(observation):
            seen.add(observation.event)
            if observation.event == "cutoff":
                return "human"
            return None

        replay(pack, self.out, detector_factory=lambda: commit)
        self.assertEqual(
            seen, {"call_answered", "connected", "start", "media", "cutoff", "stop", "call_completed"}
        )
        traces = self.read_traces()
        self.assertEqual(len(traces), 6)
        for trace in traces.values():
            self.assertEqual(trace["initial_decision"]["decision"], "human")
            self.assertEqual(trace["policy_events"][0]["event"], "screener")
        scorecard = self.read_scorecard()
        self.assert_rate(scorecard["acoustic"]["committed_coverage"], 0, 0, None)
        self.assert_rate(scorecard["acoustic"]["committed_error"], 0, 0, None)
        self.assertEqual(scorecard["policy_assertions"]["passed"], 6)

    def test_wrong_voicemail_routing_uses_playback_and_all_human_fixtures(self):
        pack, manifest, entries = self.copy_pack()
        case_id = "simulated-screener-caller-to-human"
        case = next(case for case in entries["cases"] if case["id"] == case_id)
        case["policy_events"][-1]["event"] = "mailbox_ready"
        case["policy_events"].append({"event": "disconnect", "at_ms": 2440})
        manifest.write_bytes(canonical_json(entries))

        replay(pack, self.out)
        scorecard = self.read_scorecard()
        self.assert_rate(scorecard["wrong_voicemail_routing"], 1, 7, 1 / 7)
        failures = [case for case in scorecard["policy_assertions"]["cases"] if not case["passed"]]
        self.assertEqual([failure["case_id"] for failure in failures], [case_id])
        self.assertEqual(failures[0]["actual_actions"][-2:], [
            {"action": "play_voicemail", "at_ms": 2420},
            {"action": "cancel_playback", "at_ms": 2440},
        ])

    def test_cli_reproduces_golden_bytes_across_directories_and_hash_seeds(self):
        for out, seed, detector_args in (
            (self.out, "1", ()),
            (self.temp / "other", "867", ()),
            (self.out, "3", ("--detector", "v1")),
            (self.temp / "other", "71", ("--detector", "v1")),
        ):
            subprocess.run(
                [*REPLAY_ARGS, "--out", out, *detector_args],
                cwd=ROOT,
                env={**os.environ, "PYTHONHASHSEED": seed},
                check=True,
            )
            for filename in ("scorecard.json", "traces.jsonl"):
                with self.subTest(seed=seed, filename=filename):
                    self.assertEqual((out / filename).read_bytes(), (EXPECTED / filename).read_bytes())

    def test_cli_rejects_unknown_detector(self):
        result = subprocess.run(
            [*REPLAY_ARGS, "--out", self.out, "--detector", "unknown"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"whoanswered: unknown detector 'unknown' (choose from v1)\n")
        self.assertFalse(self.out.exists())
