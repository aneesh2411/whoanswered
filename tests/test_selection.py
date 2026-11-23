import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_release import DENY_SOCKETS
from tests.test_veto import ACTIVE, QUIET
from whoanswered.fixtures import CUTOFF_MS, FRAME_MS, canonical_json
from whoanswered.replay import Observation
from whoanswered.veto import SCORE_THRESHOLD_FRAMES, EvidenceScore


ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "fixtures/v2/dev"
DEV_MANIFEST_SHA256 = "e08648ebe8497c24eed38eb2dd05d9bfe728ad8461d1fc2176b060b0c42c4a36"


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_tune(self, pack, out, seed="1"):
        return subprocess.run(
            [sys.executable, "-c", DENY_SOCKETS, "tune", "--pack", pack, "--out", out],
            cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True,
        )

    def test_pinned_dev_counts_and_selection_are_byte_stable(self):
        self.assertEqual(hashlib.sha256((DEV / "manifest.json").read_bytes()).hexdigest(),
                         DEV_MANIFEST_SHA256)
        outputs = []
        for index, seed in enumerate(("1", "1", "867")):
            out = self.temp / str(index)
            result = self.run_tune(DEV, out, seed)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(path.name for path in out.iterdir()), ["selection.json"])
            outputs.append((out / "selection.json").read_bytes())
        self.assertEqual(outputs, [outputs[0]] * 3)
        report = json.loads(outputs[0])
        self.assertEqual(outputs[0], canonical_json(report))

        def counts(metrics):
            return tuple(metrics[name]["numerator"] for name in (
                "committed_coverage", "committed_error", "correct_over_all_eligible",
            ))

        self.assertEqual(report["split"], "dev")
        self.assertEqual(len(report["eligible_case_ids"]), 18)
        self.assertEqual(counts(report["acoustic"]["v1"]), (10, 4, 6))
        self.assertEqual(counts(report["acoustic"]["veto"]), (4, 1, 3))
        self.assertEqual({
            family: (values["lost_correct_commitments"], values["avoided_errors"])
            for family, values in report["veto_vs_v1_by_family"].items()
        }, {
            "long-human": (0, 0),
            "paused-mailbox": (0, 3),
            "resumed-human": (3, 0),
            "short-human": (0, 0),
            "short-mailbox": (0, 0),
        })

        trials = {trial["threshold_frames"]: trial for trial in report["score_trials"]}
        self.assertEqual({threshold: counts(trial["acoustic"]) for threshold, trial in trials.items()},
                         {6: (5, 1, 4), 12: (5, 1, 4), 24: (4, 1, 3)})
        self.assertTrue(trials[12]["matched_prefix_assertions"][0]["passed"])
        self.assertEqual(trials[12]["vs_veto_by_family"]["resumed-human"]["gained_correct_commitments"], 1)
        self.assertEqual(report["selection"]["detector"], "score")
        self.assertEqual(report["selection"]["threshold_frames"], 12)
        self.assertEqual(report["selection"]["threshold_frames"], SCORE_THRESHOLD_FRAMES)

    def test_tuning_rejects_eval_and_mixed_manifests_before_payloads(self):
        pack = self.temp / "pack"
        out = self.temp / "out"
        pack.mkdir()
        for splits in (("eval",), ("dev", "eval"), ("eval", "dev")):
            with self.subTest(splits=splits):
                (pack / "manifest.json").write_bytes(canonical_json({
                    "schema_version": 1,
                    "pack_version": 2,
                    "cases": [{"id": str(index), "split": split} for index, split in enumerate(splits)],
                }))
                result = self.run_tune(pack, out)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, b"whoanswered: DEV_SPLIT_REQUIRED: manifest.json\n")
                self.assertFalse(out.exists())

    def test_tuning_keeps_veto_when_no_correct_commitment_is_lost_to_resumption(self):
        pack = self.temp / "pack"
        out = self.temp / "out"
        case_id = "dev-short-human-clean"
        shutil.copytree(DEV / case_id, pack / case_id)
        manifest = json.loads((DEV / "manifest.json").read_bytes())
        manifest["cases"] = [case for case in manifest["cases"] if case["id"] == case_id]
        (pack / "manifest.json").write_bytes(canonical_json(manifest))

        result = self.run_tune(pack, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((out / "selection.json").read_bytes())
        self.assertEqual(report["score_trials"], [])
        self.assertEqual(report["selection"]["detector"], "veto")
        self.assertIsNone(report["selection"]["threshold_frames"])


class EvidenceScoreTest(unittest.TestCase):
    def test_threshold_is_inclusive_for_both_evidence_counts(self):
        for resumed, trailing in ((11, 58), (12, 58), (13, 57), (40, 11), (40, 12), (40, 13)):
            with self.subTest(resumed=resumed, trailing=trailing):
                detector = EvidenceScore()
                resume_end = CUTOFF_MS // FRAME_MS - trailing
                for index, start_ms in enumerate(range(0, CUTOFF_MS, FRAME_MS)):
                    audio = ACTIVE if index < 6 or resume_end - resumed <= index < resume_end else QUIET
                    self.assertIsNone(detector(Observation(
                        "media", start_ms + FRAME_MS, sample_start_ms=start_ms, audio=audio,
                    )))
                update = detector(Observation("cutoff", CUTOFF_MS))
                self.assertEqual(update.verdict, "human" if min(resumed, trailing) >= 12 else "uncertain")
