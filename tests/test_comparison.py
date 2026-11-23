import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_release import DENY_SOCKETS
from whoanswered.comparison import compare
from whoanswered.fixtures import canonical_json, canonical_jsonl
from whoanswered.replay import Observation


ROOT = Path(__file__).resolve().parents[1]
CLI_ARGS = ("compare", "--pack", "fixtures/v2/eval")
ELIGIBLE = ["h-late", "h-right", "h-wrong", "m-abstain", "m-right", "m-wrong"]


def pinned_pack(pack):
    pack.mkdir()
    (pack / "audio.ulaw").write_bytes(b"")
    cases = []
    for name, truth, time, family in (
        ("dev-human", "human", 17, "opening"),
        ("disconnect", "disconnect", 20, "other-truth"),
        ("h-late", "human", 2001, "opening"),
        ("h-right", "human", 100, "pause"),
        ("h-wrong", "human", 201, "opening"),
        ("m-abstain", "voicemail", 250, None),
        ("m-right", "voicemail", 2000, "opening"),
        ("m-wrong", "voicemail", 400, "pause"),
        ("other-track", "human", 25, "other-track"),
        ("pair-human", "human", 50, "matched"),
        ("pair-mailbox", "voicemail", 50, "matched"),
        ("policy", "human", 30, "scripted"),
        ("silence", "silence", 20, "other-truth"),
        ("unknown", "unknown", 20, "other-truth"),
    ):
        is_policy = name == "policy"
        case = {
            "id": name,
            "license": "MIT",
            "provenance": "Scripted, empty-audio comparison test case.",
            "source_group": "pair" if name.startswith("pair-") else name,
            "split": "dev" if name == "dev-human" else "eval",
            "track": {"policy": "policy", "other-track": "other"}.get(name, "acoustic"),
            "payloads": {
                "audio.ulaw": {"path": "audio.ulaw", "sha256": hashlib.sha256(b"").hexdigest()},
            },
        }
        if family is not None:
            case["family"] = family
        if is_policy:
            case["policy_events"] = [{"event": "human_takeover", "at_ms": 2400}]
        (pack / name).mkdir()
        for filename, payload in (
            ("events.jsonl", canonical_jsonl([
                {"event": "call_answered", "arrival_ms": 0, "sequence": 1},
                {"event": "stop", "arrival_ms": time, "sequence": 2},
            ])),
            ("expected.json", canonical_json({
                "answerer": truth,
                "matched_pair": "pair" if name.startswith("pair-") else None,
                "verdict": "uncertain",
                "actions": [{"action": "start_conversation", "at_ms": 2400}] if is_policy else [],
            })),
        ):
            relative = f"{name}/{filename}"
            (pack / relative).write_bytes(payload)
            case["payloads"][filename] = {"path": relative, "sha256": hashlib.sha256(payload).hexdigest()}
        cases.append(case)
    manifest = canonical_json({"schema_version": 1, "pack_version": 2, "cases": cases})
    (pack / "manifest.json").write_bytes(manifest)
    return hashlib.sha256(manifest).hexdigest()


def scripted_factory(updates):
    def choose(observation):
        if observation.event == "stop":
            return updates.get(observation.at_ms)
        return None

    return lambda: choose


SCRIPTED = {100: "human", 201: "voicemail", 400: "human", 2000: "voicemail", 2001: "human"}


def forced_human(observation):
    return "human" if observation.event == "cutoff" else None


def rate(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.pack = self.temp / "pack"
        self.out = self.temp / "out"
        self.assertEqual(pinned_pack(self.pack), "158db5c518736d6186f637b8e141fdba51e0715f13843fbec7422b2ad77e7008")

    def report(self, factories, **options):
        compare(self.pack, self.out, detector_factories=factories, **options)
        return json.loads((self.out / "comparison.json").read_bytes())

    def test_pinned_pack_denominators_and_separate_checks(self):
        report = self.report({"v1": scripted_factory(SCRIPTED)})
        self.assertEqual(report["eligible_case_ids"], ELIGIBLE)
        self.assertEqual(report["split"], "eval")
        self.assertEqual(report["cutoff_ms"], 2000)
        self.assertEqual(report["acoustic"], {"v1": {
            "correct_over_all_eligible": rate(2, 6),
            "committed_coverage": rate(4, 6),
            "committed_error": rate(2, 4),
            "human_as_mailbox": rate(1, 3),
            "mailbox_as_human": rate(1, 3),
            "abstentions_by_class": {"human": rate(1, 3), "voicemail": rate(1, 3)},
            "abstentions_by_family": {"opening": rate(1, 3), "pause": rate(0, 2), "unspecified": rate(1, 1)},
            "median_commitment_ms": 300.5,
        }})
        self.assertEqual(report["matched_prefix_assertions"], {"v1": [{
            "matched_pair": "pair",
            "identical_prefix": True,
            "initial_verdicts": {"pair-human": "uncertain", "pair-mailbox": "uncertain"},
            "passed": True,
        }]})
        self.assertEqual(report["policy_assertions"], {
            "passed": 1, "total": 1,
            "cases": [{
                "case_id": "policy", "passed": True,
                "actual_actions": [{"action": "start_conversation", "at_ms": 2400}],
                "expected_actions": [{"action": "start_conversation", "at_ms": 2400}],
            }],
        })

    def test_controls_and_identical_cutoff_inputs(self):
        seen = {"v1": [], "forced-human": [], "always-abstain": []}

        def record(name, factory):
            def fresh():
                choose = factory()
                observations = []
                seen[name].append(observations)

                def observe(observation):
                    observations.append(observation)
                    return choose(observation)

                return observe

            return fresh

        report = self.report({
            "v1": record("v1", scripted_factory(SCRIPTED)),
            "forced-human": record("forced-human", lambda: forced_human),
            "always-abstain": record("always-abstain", lambda: lambda observation: "uncertain"),
        })
        self.assertEqual(len(seen["v1"]), len(ELIGIBLE) + 2)
        self.assertEqual(seen["v1"], seen["forced-human"])
        self.assertEqual(seen["v1"], seen["always-abstain"])
        self.assertTrue(all(observations[-1] == Observation("cutoff", 2000) for observations in seen["v1"]))
        self.assertEqual(seen["v1"][0], [Observation("call_answered", 0, 1), Observation("cutoff", 2000)])

        forced = report["acoustic"]["forced-human"]
        self.assertEqual(forced["committed_coverage"], rate(6, 6))
        self.assertEqual(forced["committed_error"], rate(3, 6))
        self.assertEqual(forced["correct_over_all_eligible"], rate(3, 6))
        self.assertEqual(forced["human_as_mailbox"], rate(0, 3))
        self.assertEqual(forced["mailbox_as_human"], rate(3, 3))
        self.assertEqual(forced["median_commitment_ms"], 2000)
        self.assertFalse(report["matched_prefix_assertions"]["forced-human"][0]["passed"])

        abstain = report["acoustic"]["always-abstain"]
        self.assertEqual(abstain["committed_coverage"], rate(0, 6))
        self.assertEqual(abstain["committed_error"], rate(0, 0))
        self.assertEqual(abstain["correct_over_all_eligible"], rate(0, 6))
        self.assertEqual(abstain["human_as_mailbox"], rate(0, 3))
        self.assertEqual(abstain["mailbox_as_human"], rate(0, 3))
        self.assertEqual(abstain["abstentions_by_class"], {"human": rate(3, 3), "voicemail": rate(3, 3)})
        self.assertIsNone(abstain["median_commitment_ms"])
        self.assertTrue(report["matched_prefix_assertions"]["always-abstain"][0]["passed"])
        self.assertEqual(report["promotion"], {"forced-human": False, "always-abstain": False})

    def test_promotion_uses_counts_and_both_separate_checks(self):
        improved = SCRIPTED | {201: "human", 400: "voicemail"}
        factories = {
            "v1": scripted_factory(SCRIPTED),
            "equal": scripted_factory(SCRIPTED),
            "improved": scripted_factory(improved),
            "fails-pair": scripted_factory(improved | {50: "human"}),
            "other-wrong": scripted_factory(SCRIPTED | {400: "disconnect"}),
        }
        report = self.report(factories)
        self.assertEqual(report["promotion"], {
            "equal": False, "improved": True, "fails-pair": False, "other-wrong": False,
        })
        self.assertEqual(report["acoustic"]["other-wrong"]["committed_error"], rate(2, 4))
        self.assertEqual(report["acoustic"]["other-wrong"]["mailbox_as_human"], rate(0, 3))
        failed_policy = self.report(factories, policy_runner=lambda events, sink: None)
        self.assertFalse(failed_policy["promotion"]["improved"])
        self.assertEqual(failed_policy["policy_assertions"]["passed"], 0)
        self.assertEqual(failed_policy["acoustic"], report["acoustic"])
        self.assertEqual(failed_policy["matched_prefix_assertions"], report["matched_prefix_assertions"])

    def test_cli_requires_eval_and_accepts_an_empty_eligible_set(self):
        result = subprocess.run(
            [sys.executable, "-m", "whoanswered", "compare", "--pack", "fixtures/v2/dev", "--out", self.out],
            cwd=ROOT, capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"whoanswered: MISSING_EVAL_SPLIT: manifest.json\n")
        self.assertFalse(self.out.exists())

        manifest = self.pack / "manifest.json"
        data = json.loads(manifest.read_bytes())
        data["cases"] = [case for case in data["cases"] if case["id"] == "policy"]
        manifest.write_bytes(canonical_json(data))
        report = self.report({"v1": lambda: forced_human})
        self.assertEqual(report["eligible_case_ids"], [])
        metrics = report["acoustic"]["v1"]
        for name in ("committed_coverage", "committed_error", "correct_over_all_eligible",
                     "human_as_mailbox", "mailbox_as_human"):
            self.assertEqual(metrics[name], rate(0, 0))

    def test_cli_byte_stability_and_no_sockets(self):
        outputs = []
        for out, hash_seed, launch in (
            (self.out, "1", ["-m", "whoanswered"]),
            (self.temp / "other", "867", ["-c", DENY_SOCKETS]),
        ):
            subprocess.run(
                [sys.executable, *launch, *CLI_ARGS, "--out", out],
                cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": hash_seed}, check=True,
            )
            self.assertEqual(sorted(path.name for path in out.iterdir()), ["comparison.json"])
            outputs.append((out / "comparison.json").read_bytes())

        self.assertEqual(outputs[0], outputs[1])
        report = json.loads(outputs[0])
        self.assertEqual(outputs[0], canonical_json(report))
        self.assertEqual(report["acoustic"]["v1"]["committed_coverage"], rate(10, 18))
        self.assertEqual(report["acoustic"]["v1"]["committed_error"], rate(4, 10))
        self.assertTrue(report["matched_prefix_assertions"]["v1"][0]["passed"])
        self.assertEqual(set(report["acoustic"]), {"v1", "veto"})
        self.assertTrue(report["matched_prefix_assertions"]["veto"][0]["passed"])
