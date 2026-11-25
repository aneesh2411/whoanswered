"""Keep private data out of tracked files, reachable Git history, and wheels."""

import codecs
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from functools import cache
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]

# These manifests enumerate the reviewed, authored synthetic G.711 fixtures.
# Pin each manifest so changing its audio hashes cannot approve a replacement.
REVIEWED_MANIFESTS = {
    "fixtures/baseline/manifest.json": "1f94e0251ca629f01b105aaaebb0dc8be64494fd547ed6a402a39a21d914d2a2",
    "fixtures/smoke/manifest.json": "4fa6efcde239c1dd718a052e33da40219cdb70020c8bdd57159dc37399bfcb42",
    "fixtures/v1/manifest.json": "7d1811ffe978bbac96e0f7a1abe7e10a71652c0f53d7a0e2bdfb86465a5f981d",
    "fixtures/v2/dev/manifest.json": "e08648ebe8497c24eed38eb2dd05d9bfe728ad8461d1fc2176b060b0c42c4a36",
    "fixtures/v2/eval/manifest.json": "f3d79d16663211c094576263196e39e9a34c86e74744e59d7caf644865ee64c5",
    "fixtures/v3/dev/manifest.json": "d12c560e21287e4435e4f731ee45c5d2aef0793060a9173dc29731a30ae735e9",
    "fixtures/v3/eval/manifest.json": "6180a70b654ff20338026364f8bea64e1dfabab60cedfcd881e67860e6d08544",
}
ALLOWED_MATCHES = {
    ("email address", b"36195616+aneesh2411@users.noreply.github.com"):
        "Public GitHub noreply identity used for project commits.",
}

AUTH_LABEL = rb"(?<![a-z0-9])(?:auth(?:[ _-]?token)?|token)(?![a-z0-9])"
HEX_TOKEN = rb"(?<![a-z0-9])[a-f0-9]{32}(?![a-z0-9])"
AUTH_SEPARATOR = rb"""[\s"'\x60:=,()<>-]{1,32}"""
PATTERNS = (
    ("Twilio SID", re.compile(rb"(?<![a-z0-9])(?:AC|SK|CA|RE|PN|MZ)[0-9a-f]{32}(?![a-z0-9])", re.I)),
    ("auth token", re.compile(
        AUTH_LABEL + AUTH_SEPARATOR + HEX_TOKEN + b"|" + HEX_TOKEN + AUTH_SEPARATOR + AUTH_LABEL, re.I
    )),
    ("GitHub token", re.compile(
        rb"(?<![a-z0-9])(?:gh[pousr]_[a-z0-9]{36,}|github_pat_[a-z0-9_]{20,})(?![a-z0-9_])", re.I
    )),
    ("private key", re.compile(
        rb"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY(?: BLOCK)?-----|PuTTY-User-Key-File-[23]:|openssh-key-v1\0"
    )),
    ("phone number", re.compile(
        rb"(?<![a-z0-9+])(?:\+[1-9][0-9]{6,14}|"
        rb"(?:\+?1[-. \t]?)?(?:\([0-9]{3}\)[ \t]*|[0-9]{3}[-. \t]?)[0-9]{3}[-. \t]?[0-9]{4})"
        rb"(?![a-z0-9])", re.I
    )),
    ("email address", re.compile(
        rb"(?<![a-z0-9_.%+-])[a-z0-9.!#$%&'*+/=?^_{|}~-]{1,64}@"
        rb"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?![a-z0-9-])", re.I
    )),
    ("vault data", re.compile(rb"^\$ANSIBLE_VAULT;", re.MULTILINE)),
)

AUDIO_SUFFIXES = {
    ".aac", ".aif", ".aifc", ".aiff", ".alaw", ".amr", ".au", ".caf", ".flac", ".gsm",
    ".m4a", ".m4b", ".mp3", ".mulaw", ".oga", ".ogg", ".opus", ".pcm", ".raw", ".snd", ".spx", ".ulaw",
    ".wav", ".wave", ".wma",
}
AUDIO_HEADER = re.compile(
    rb"(?:RIFF....WAVE|RIFX....WAVE|RF64....WAVE|FORM....AIF[CF]|"
    rb"fLaC|OggS|ID3|\.snd|#!AMR|caff|....ftyp(?:M4A |M4B |M4P ))", re.DOTALL
)


def git(*args, input=None):
    return subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=ROOT, input=input, stdout=subprocess.PIPE, check=True
    ).stdout


def git_files(records):
    for record in records.split(b"\0")[:-1]:
        metadata, name = record.split(b"\t", 1)
        yield os.fsdecode(name), metadata.split()


def read_worktree(paths):
    files = {}
    for name in paths:
        path = ROOT / name
        if path.is_symlink():
            files[name] = os.fsencode(os.readlink(path))
        elif path.is_file() and path.resolve().is_relative_to(ROOT):
            files[name] = path.read_bytes()
    return files


def reviewed_audio(files):
    audio = {}
    for name, digest in REVIEWED_MANIFESTS.items():
        manifest = files.get(name)
        if manifest is None or hashlib.sha256(manifest).hexdigest() != digest:
            continue
        for case in json.loads(manifest)["cases"]:
            payload = case["payloads"]["audio.ulaw"]
            path = PurePosixPath(name).parent / payload["path"]
            audio[path.as_posix()] = payload["sha256"]
    return audio


def private_matches(data):
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(data):
            if (kind, match[0]) not in ALLOWED_MATCHES:
                yield kind, match


def safe_path(name):
    data = os.fsencode(name)
    redacted = bytearray(data)
    for _, match in private_matches(data):
        start, end = match.span()
        redacted[start:end] = b"*" * (end - start)
    return repr(os.fsdecode(bytes(redacted)))


def is_audio(path, data):
    if path.suffix.lower() in AUDIO_SUFFIXES or AUDIO_HEADER.match(data):
        return True
    # An MPEG audio frame can begin immediately if there is no ID3 tag.
    return (
        len(data) >= 4 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
        and data[1] & 0x18 != 0x08 and data[1] & 0x06 != 0
        and data[2] & 0xF0 not in (0, 0xF0) and data[2] & 0x0C != 0x0C
    )


@cache
def file_problems(name, data, audio_digest):
    problems = []
    path = PurePosixPath(name.lower())
    if path.name == ".env" or path.name.startswith(".env.") or path.suffix == ".env":
        problems.append("local environment file")
    if {"vault", ".vault"} & set(path.parts) or path.name in {"vault.json", "vault.yaml", "vault.yml"}:
        problems.append("vault file")
    if "recordings" in path.parts[:-1]:
        problems.append("recordings directory")
    if is_audio(path, data) and hashlib.sha256(data).hexdigest() != audio_digest:
        problems.append("audio outside the reviewed synthetic fixture payloads")

    problems.extend(f"{kind} in path" for kind, _ in private_matches(os.fsencode(name)))
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        data = data.decode("utf-32", errors="replace").encode()
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        data = data.decode("utf-16", errors="replace").encode()
    for kind, match in private_matches(data):
        line = data.count(b"\n", 0, match.start()) + 1
        problems.append(f"{kind} on line {line}")
    return tuple(sorted(set(problems)))


def scan(source, files, reported):
    audio = reviewed_audio(files)
    for name, data in sorted(files.items()):
        for problem in file_problems(name, data, audio.get(name)):
            record = (name, data, problem)
            if record not in reported:
                reported.add(record)
                yield f"{source} {safe_path(name)}: {problem}"


class NoSecretsTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(file_problems.cache_clear)

    def assert_clean(self, problems):
        self.assertFalse(problems, "\n" + "\n".join(problems))

    def test_tracked_files_and_history(self):
        index = {}
        for name, (mode, oid, stage) in git_files(git("ls-files", "--stage", "-z")):
            if stage != b"0":
                self.fail(f"Unmerged index entry for {safe_path(name)}")
            if mode != b"160000":  # Submodules are references to other repositories.
                index[name] = oid

        @cache
        def blob(oid):
            return git("cat-file", "blob", oid)

        reported = set()
        problems = list(scan("worktree", read_worktree(index), reported))
        problems.extend(scan("index", {name: blob(oid) for name, oid in index.items()}, reported))

        trees = set()
        for commit, tree in sorted(line.split() for line in git("log", "--all", "HEAD", "--format=%H %T").splitlines()):
            if tree in trees:
                continue
            trees.add(tree)
            files = {
                name: blob(oid)
                for name, (_, kind, oid) in git_files(git("ls-tree", "-rz", tree))
                if kind == b"blob"
            }
            problems.extend(scan(f"commit {commit.decode()[:12]}", files, reported))
        self.assert_clean(problems)

    def test_built_wheel(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = temp / "source"

        def ignore_build_outputs(directory, names):
            ignored = {"__pycache__"}
            if Path(directory) == ROOT:
                ignored.update({".git", "build", "dist"})
                ignored.update(name for name in names if name.endswith(".egg-info"))
            return ignored.intersection(names)

        # Build the project with its full source tree so packaging changes are
        # checked against their actual inputs, including ignored package data.
        shutil.copytree(ROOT, source, symlinks=True, ignore=ignore_build_outputs)
        subprocess.run([
            sys.executable, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "wheel", "--no-cache-dir", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir", temp, source,
        ], cwd=temp, check=True, stdout=subprocess.DEVNULL)
        wheel, = temp.glob("*.whl")
        with ZipFile(wheel) as archive:
            files = {}
            for entry in archive.infolist():
                if not entry.is_dir():
                    if entry.filename in files:
                        self.fail(f"Duplicate wheel entry {safe_path(entry.filename)}")
                    files[entry.filename] = archive.read(entry)
        self.assert_clean(list(scan("wheel", files, set())))
