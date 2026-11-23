import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PACK = Path("fixtures/v1")
EXPECTED = ROOT / "tests" / "expected" / "v1"
REPLAY_OUT = Path("build/replay")
REPLAY_ARGS = ("replay", "--pack", PACK.as_posix(), "--out", REPLAY_OUT.as_posix())
DENY_SOCKETS = """\
import runpy
import sys

attempts = []

def deny_socket(event, args):
    if event.startswith("socket."):
        attempts.append(event)
        raise AssertionError(f"Replay attempted {event}")

sys.addaudithook(deny_socket)
runpy.run_module("whoanswered", run_name="__main__", alter_sys=True)
if attempts:
    raise AssertionError(f"Replay attempted socket operations: {attempts}")
"""


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.archive = self.temp / "build" / "whoanswered-v1.zip"
        self.build(PACK, self.archive)

    def build(self, pack, archive):
        subprocess.run(
            [sys.executable, "-m", "whoanswered", "bundle", "--pack", pack, "--out", archive],
            cwd=ROOT,
            check=True,
        )

    def test_bundle_bytes_contents_and_metadata(self):
        copy = self.temp / "copy"
        shutil.copytree(ROOT / PACK, copy, copy_function=shutil.copyfile)
        (copy / "unrelated.txt").write_text("Local working file", encoding="utf-8")
        second = self.temp / "second.zip"
        self.build(copy, second)
        self.assertEqual(
            hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            hashlib.sha256(second.read_bytes()).hexdigest(),
        )

        sources = {
            "LICENSE": ROOT / "LICENSE",
            "README.md": ROOT / "README.md",
            "fixtures/LICENSE.md": ROOT / "fixtures" / "LICENSE.md",
            "reference/scorecard.json": EXPECTED / "scorecard.json",
        }
        manifest = ROOT / PACK / "manifest.json"
        sources[(PACK / "manifest.json").as_posix()] = manifest
        sources[(PACK / "README.md").as_posix()] = ROOT / PACK / "README.md"
        for case in json.loads(manifest.read_bytes())["cases"]:
            for payload in case["payloads"].values():
                path = PACK / payload["path"]
                sources[path.as_posix()] = ROOT / path
        for module in (ROOT / "whoanswered").glob("*.py"):
            sources[f"whoanswered/{module.name}"] = module

        with ZipFile(self.archive) as archive:
            self.assertEqual(archive.namelist(), sorted(sources))
            for entry in archive.infolist():
                with self.subTest(entry=entry.filename):
                    self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(entry.create_system, 3)
                    self.assertEqual(entry.external_attr >> 16, 0o100644)
                    self.assertEqual(entry.compress_type, ZIP_STORED)
                    self.assertEqual(archive.read(entry), sources[entry.filename].read_bytes())

    def test_clean_extraction_reproduces_golden_replay(self):
        for socket_denied, launch in ((False, ["-m", "whoanswered"]), (True, ["-c", DENY_SOCKETS])):
            with self.subTest(socket_denied=socket_denied):
                extracted = Path(self.enterContext(tempfile.TemporaryDirectory(dir=self.temp)))
                with ZipFile(self.archive) as archive:
                    archive.extractall(extracted)
                out = extracted / REPLAY_OUT
                self.assertFalse(out.exists())
                subprocess.run(
                    [sys.executable, "-E", "-S", *launch, *REPLAY_ARGS],
                    cwd=extracted,
                    env={},
                    check=True,
                )
                for name in ("scorecard.json", "traces.jsonl"):
                    self.assertEqual((out / name).read_bytes(), (EXPECTED / name).read_bytes())
                self.assertEqual(
                    (out / "scorecard.json").read_bytes(),
                    (extracted / "reference" / "scorecard.json").read_bytes(),
                )

                readme = (extracted / "README.md").read_text(encoding="utf-8")
                command = " ".join(("python", "-m", "whoanswered", *REPLAY_ARGS))
                self.assertIn(f"\n{command}\n", readme)
                for filename in (
                    "clean-pickup-hello/result.json",
                    "clean-pickup-hello/trace.json",
                    "matched-hello-human/trace.json",
                    "matched-hello-mailbox/trace.json",
                    "scorecard.json",
                    "traces.jsonl",
                ):
                    self.assertIn(f"`{(REPLAY_OUT / filename).as_posix()}`", readme)
                    self.assertTrue((out / filename).is_file())
                self.assertIn("`simulated-screener-caller-to-human`", readme)
