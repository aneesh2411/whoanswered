import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "tests" / "expected" / "smoke"


class SmokeTest(unittest.TestCase):
    def test_replay_matches_expectations(self) -> None:
        pack = Path("fixtures/smoke")
        self.assertEqual((ROOT / pack / "greeting.txt").read_bytes(), b"Hello?\n")

        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "smoke"
            subprocess.run(
                [sys.executable, "-m", "whoanswered", "replay", "--pack", pack, "--out", out],
                cwd=ROOT,
                check=True,
            )

            for filename in ("result.json", "trace.json"):
                with self.subTest(filename=filename):
                    self.assertEqual(
                        (out / filename).read_bytes(),
                        (EXPECTED / filename).read_bytes(),
                    )
