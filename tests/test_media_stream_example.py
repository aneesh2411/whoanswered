import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MediaStreamExampleTest(unittest.TestCase):
    def test_offline_example_reports_commitment_and_abstention(self):
        result = subprocess.run(
            [sys.executable, "-m", "examples.media_stream_example"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "clean-pickup-hello: human at 800 ms (committed, short_greeting_then_pause)",
                "connected-silence-digital: uncertain at 2000 ms (uncommitted, connected_silence)",
            ],
        )
