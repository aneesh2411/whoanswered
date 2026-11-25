import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


class PackageTest(unittest.TestCase):
    def test_offline_wheel_install_and_docs_example(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = temp / "source"
        source.mkdir()
        for filename in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copyfile(ROOT / filename, source / filename)
        shutil.copytree(ROOT / "whoanswered", source / "whoanswered", ignore=shutil.ignore_patterns("__pycache__"))

        self.run_checked([
            sys.executable, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir", temp, source,
        ], temp)
        wheels = list(temp.glob("*.whl"))
        self.assertEqual([wheel.name for wheel in wheels], ["whoanswered-0.1.0-py3-none-any.whl"])
        wheel = wheels[0]
        with ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata = BytesParser().parsebytes(archive.read("whoanswered-0.1.0.dist-info/METADATA"))
            self.assertEqual(metadata["Name"], "whoanswered")
            self.assertEqual(metadata["Version"], "0.1.0")
            self.assertEqual(metadata["Requires-Python"], ">=3.12")
            self.assertEqual(metadata["License-Expression"], "MIT")
            self.assertIsNone(metadata.get_all("Requires-Dist"))
            self.assertIn("whoanswered/py.typed", names)
            self.assertFalse(any(name.startswith("fixtures/") for name in names))

        env = temp / "venv"
        self.run_checked([sys.executable, "-I", "-m", "venv", env], temp)
        scripts = env / ("Scripts" if sys.platform == "win32" else "bin")
        python = scripts / ("python.exe" if sys.platform == "win32" else "python")
        self.run_checked([
            python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "install", "--no-index", "--no-deps", wheel,
        ], temp)
        help_text = self.run_checked([python, "-I", "-m", "whoanswered", "--help"], temp)
        self.assertIn("usage: python -m whoanswered", help_text)
        cli = scripts / ("whoanswered.exe" if sys.platform == "win32" else "whoanswered")
        self.assertEqual(self.run_checked([cli, "--help"], temp), help_text)

        docs = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
        example, = re.findall(r"^```python\n(.*?)^```$", docs, re.MULTILINE | re.DOTALL)
        self.assertEqual(len(example.splitlines()), 15)
        self.assertEqual(self.run_checked([python, "-I", "-c", example], temp), "human\n")

    def run_checked(self, command, cwd):
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout
