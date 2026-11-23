import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from whoanswered.baseline import AudioBaseline
from whoanswered.fixtures import (
    CUTOFF_MS, FRAME_BYTES, FRAME_MS, SILENT_FRAME, load_pack, validate_split_boundary,
)
from whoanswered.replay import Observation, replay


ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "fixtures" / "v2"
# A manifest digest pins the complete set of payload hashes as well as the split,
# membership, and evaluator assertions. Freeze eval before any detector tuning.
MANIFEST_SHA256 = {
    "dev": "e08648ebe8497c24eed38eb2dd05d9bfe728ad8461d1fc2176b060b0c42c4a36",
    "eval": "f3d79d16663211c094576263196e39e9a34c86e74744e59d7caf644865ee64c5",
}
CONTINUATION_VARIANTS = {
    "before", "before-media-gap", "near", "near-room-tone", "after", "after-media-gap",
}
# Each row gives the dev seed, eval seed, truth, and variants for one source family.
MATRIX = {
    "short-human": (21901, 21911, "human", {"clean", "room-tone"}),
    "short-mailbox": (21902, 21912, "voicemail", {"tone", "room-tone"}),
    "long-human": (21903, 21913, "human", {"clean", "media-gap"}),
    "resumed-human": (21904, 21914, "human", CONTINUATION_VARIANTS),
    "paused-mailbox": (21905, 21915, "voicemail", CONTINUATION_VARIANTS),
    "matched-prefix": (21906, 21916, None, {"human", "mailbox"}),
}
RESUME_MS = {
    "dev": {"before": 980, "near": 1980, "after": 2240, "matched-prefix": 2180},
    "eval": {"before": 1140, "near": 1980, "after": 2320, "matched-prefix": 2320},
}
OFFLINE_MODULE = """\
import runpy
import sys

attempts = []

def deny_sockets(event, args):
    if event.startswith("socket."):
        attempts.append(event)
        raise AssertionError(event)

sys.addaudithook(deny_sockets)
runpy.run_module(sys.argv.pop(1), run_name="__main__", alter_sys=True)
if attempts:
    raise AssertionError(attempts)
"""


def run_offline(module, *args):
    subprocess.run(
        [sys.executable, "-E", "-S", "-c", OFFLINE_MODULE, module, *args],
        cwd=ROOT, env={}, check=True,
    )


def non_silent(frame):
    return frame.replace(b"\x7f", b"\xff") != SILENT_FRAME


class V2PacksTest(unittest.TestCase):
    def test_both_splits_validate_offline(self):
        for packs in (("dev",), ("eval",), ("dev", "eval")):
            with self.subTest(packs=packs):
                args = [arg for split in packs for arg in ("--pack", PACKS / split)]
                run_offline("whoanswered", "validate", *args)

        # Public v1 sources are available for development under revision 2 rules.
        public = load_pack(PACKS.parent / "v1")
        validate_split_boundary([*(replace(case, split="dev") for case in public),
                                 *load_pack(PACKS / "eval")])

    def test_frozen_asset_hashes_and_offline_regeneration(self):
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        run_offline("fixtures.generate_v2", "--out", out)
        for split, digest in MANIFEST_SHA256.items():
            pack = PACKS / split
            raw = (pack / "manifest.json").read_bytes()
            with self.subTest(split=split):
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
                paths = {Path("manifest.json")}
                for entry in json.loads(raw)["cases"]:
                    for filename, payload in entry["payloads"].items():
                        path = Path(entry["id"]) / filename
                        with self.subTest(path=path):
                            self.assertEqual(payload["path"], path.as_posix())
                            self.assertEqual(hashlib.sha256((pack / path).read_bytes()).hexdigest(),
                                             payload["sha256"])
                        paths.add(path)
                for directory in (pack, out / split):
                    self.assertEqual({p.relative_to(directory) for p in directory.rglob("*")
                                      if p.is_file()}, paths)
                for path in sorted(paths):
                    with self.subTest(regenerated=path):
                        self.assertEqual((pack / path).read_bytes(), (out / split / path).read_bytes())

    def test_case_family_matrix_and_source_group_membership(self):
        for split_index, split in enumerate(("dev", "eval")):
            pack = PACKS / split
            manifest = json.loads((pack / "manifest.json").read_bytes())
            self.assertEqual((manifest["schema_version"], manifest["pack_version"]), (1, 2))
            cases = {case.case_id: case for case in load_pack(pack)}
            entries = {entry["id"]: entry for entry in manifest["cases"]}
            expected_ids = set()
            for family, row in MATRIX.items():
                dev_seed, eval_seed, answerer, variants = row
                group = f"{split}-{family}"
                for variant in variants:
                    case_id = f"{group}-{variant}"
                    expected_ids.add(case_id)
                    with self.subTest(case=case_id):
                        case, entry = cases[case_id], entries[case_id]
                        self.assertEqual((case.split, case.source_group), (split, group))
                        self.assertEqual((entry["family"], entry["seed"]),
                                         (family, (dev_seed, eval_seed)[split_index]))
                        self.assertEqual((case.track, case.license), ("acoustic", "MIT"))
                        self.assertIn("First-party synthetic audio and scripted events.",
                                      case.provenance)
                        if answerer is not None:
                            self.assertEqual(case.expected.answerer, answerer)
                            self.assertIsNone(case.expected.matched_pair)
                        else:
                            self.assertEqual(case.expected.matched_pair, group)
            self.assertEqual(set(cases), expected_ids)

    def test_continuation_audio_and_transport_cross_the_cutoff(self):
        for split, times in RESUME_MS.items():
            self.assertLess(times["before"] + FRAME_MS, CUTOFF_MS)
            self.assertEqual(times["near"] + FRAME_MS, CUTOFF_MS)
            self.assertGreater(times["after"], CUTOFF_MS)
            cases = {case.case_id: case for case in load_pack(PACKS / split)}
            for family in ("resumed-human", "paused-mailbox"):
                for timing in ("before", "near", "after"):
                    case_id = f"{split}-{family}-{timing}"
                    with self.subTest(case=case_id):
                        base = cases[case_id].observable
                        resume = times[timing] // FRAME_MS
                        self.assertEqual(base.frame(resume - 1), SILENT_FRAME)
                        self.assertTrue(non_silent(base.frame(resume)))
                        arrival = next(event.arrival_ms for event in base.events
                                       if event.frame_index == resume)
                        self.assertEqual(arrival, times[timing] + FRAME_MS)
                        if timing == "near":
                            noisy = cases[f"{case_id}-room-tone"].observable
                            self.assertEqual(base.events, noisy.events)
                            self.assertEqual(base.frame(resume), noisy.frame(resume))
                            self.assertTrue(non_silent(noisy.frame(resume - 1)))
                        else:
                            gap = cases[f"{case_id}-media-gap"].observable
                            self.assertEqual(base.audio, gap.audio)
                            dropped = ({event.frame_index for event in base.events}
                                       - {event.frame_index for event in gap.events})
                            self.assertEqual(dropped, set(range(600 // FRAME_MS, 800 // FRAME_MS)))

            long_greeting = cases[f"{split}-long-human-clean"].observable
            for start, end in ((1600, CUTOFF_MS), (CUTOFF_MS, 2400)):
                with self.subTest(split=split, interval=(start, end)):
                    self.assertTrue(any(non_silent(long_greeting.frame(index))
                                        for index in range(start // FRAME_MS, end // FRAME_MS)))

    def test_each_matched_prefix_and_late_divergence(self):
        for split, times in RESUME_MS.items():
            with self.subTest(split=split):
                pack = PACKS / split
                cases = load_pack(pack)
                captures = []

                def detector_factory():
                    stream = []
                    captures.append(stream)
                    detector = AudioBaseline()

                    def record(observation):
                        stream.append(observation)
                        return detector(observation)

                    return record

                out = Path(self.enterContext(tempfile.TemporaryDirectory()))
                replay(pack, out, detector_factory=detector_factory)
                scorecard = json.loads((out / "scorecard.json").read_bytes())
                group = f"{split}-matched-prefix"
                self.assertEqual(scorecard["matched_prefix_assertions"], [{
                    "matched_pair": group,
                    "identical_prefix": True,
                    "initial_verdicts": {f"{group}-human": "uncertain",
                                         f"{group}-mailbox": "uncertain"},
                    "passed": True,
                }])

                pair = [(case, stream) for case, stream in zip(cases, captures, strict=True)
                        if case.expected.matched_pair is not None]
                self.assertEqual([case.expected.answerer for case, _ in pair], ["human", "voicemail"])
                self.assertEqual({case.expected.verdict for case, _ in pair}, {"uncertain"})
                (_, left), (_, right) = pair
                cutoff = left.index(Observation("cutoff", CUTOFF_MS))
                self.assertEqual(left[:cutoff + 1], right[:cutoff + 1])
                frames = [obs for obs in left[:cutoff] if obs.event == "media"]
                self.assertEqual([obs.sample_start_ms for obs in frames],
                                 list(range(0, CUTOFF_MS, FRAME_MS)))
                self.assertEqual(frames[-1].at_ms, CUTOFF_MS)
                self.assertTrue(any(non_silent(obs.audio) for obs in frames))

                (human, _), (mailbox, _) = pair
                resume = times["matched-prefix"]
                prefix_bytes = resume // FRAME_MS * FRAME_BYTES
                self.assertEqual(human.observable.audio[:prefix_bytes],
                                 mailbox.observable.audio[:prefix_bytes])
                a, b = next((a, b) for a, b in zip(left, right, strict=True) if a != b)
                # Both dev continuations start with the same synthesized "I'm".
                self.assertGreaterEqual(a.sample_start_ms, resume)
                self.assertEqual(a.sample_start_ms, b.sample_start_ms)
                self.assertEqual(a.at_ms, a.sample_start_ms + FRAME_MS)
                self.assertGreater(a.at_ms, CUTOFF_MS)
                self.assertEqual((a.event, b.event), ("media", "media"))
                self.assertNotEqual(a.audio, b.audio)


if __name__ == "__main__":
    unittest.main()
