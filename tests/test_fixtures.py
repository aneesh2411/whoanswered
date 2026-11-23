import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from whoanswered.fixtures import FixtureValidationError, canonical_json, canonical_jsonl, load_pack
from whoanswered.replay import replay


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "fixtures" / "smoke"


class FixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.pack = self.temp / "pack"
        shutil.copytree(SMOKE, self.pack)
        self.manifest = json.loads((self.pack / "manifest.json").read_bytes())
        self.case = self.manifest["cases"][0]
        event_lines = (self.pack / "events.jsonl").read_bytes().splitlines()
        self.events = [json.loads(line) for line in event_lines]

    def write_manifest(self) -> None:
        (self.pack / "manifest.json").write_bytes(canonical_json(self.manifest))

    def write_payload(
        self, name: str, data: bytes, *, case: dict | None = None, directory: str = ""
    ) -> None:
        case = self.case if case is None else case
        path = self.pack / directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        case["payloads"][name] = {
            "path": path.relative_to(self.pack).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        self.write_manifest()

    def assert_invalid(self, code: str) -> None:
        with self.assertRaises(FixtureValidationError) as caught:
            load_pack(self.pack)
        self.assertEqual(caught.exception.code, code)

    def test_load_separates_observable_input_from_assertions(self) -> None:
        (case,) = load_pack(self.pack)
        self.assertEqual(case.case_id, "smoke")
        self.assertEqual(case.track, "acoustic")
        self.assertEqual(case.observable.frame(0), b"\xff" * 160)
        self.assertEqual(
            [event.event for event in case.observable.events],
            ["call_answered", "start", "media", "media", "stop"],
        )
        self.assertEqual(case.expected.answerer, "silence")
        self.assertIsNone(case.expected.matched_pair)
        self.assertEqual(case.expected.verdict, "uncertain")
        self.assertEqual(case.expected.actions, ())

        self.write_payload("expected.json", canonical_json({
            "answerer": "voicemail",
            "matched_pair": None,
            "verdict": "voicemail",
            "actions": [{"action": "play_voicemail", "at_ms": 2000}],
        }))
        (changed,) = load_pack(self.pack)
        self.assertEqual(changed.observable, case.observable)
        self.assertNotEqual(changed.expected, case.expected)
        self.assertEqual(changed.expected.actions[0].at_ms, 2000)
        self.assertFalse(hasattr(changed.observable, "expected"))
        self.assertFalse(hasattr(changed.observable, "case_id"))

    def test_manifest_versions(self) -> None:
        for field, code in (
            ("schema_version", "UNSUPPORTED_SCHEMA_VERSION"),
            ("pack_version", "UNSUPPORTED_PACK_VERSION"),
        ):
            for invalid in (2, True, "1", None):
                with self.subTest(field=field, invalid=invalid):
                    self.manifest[field] = invalid
                    self.write_manifest()
                    self.assert_invalid(code)
            self.manifest[field] = 1

    def test_duplicate_and_unsorted_case_ids(self) -> None:
        second = copy.deepcopy(self.case)
        self.manifest["cases"].append(second)
        self.write_manifest()
        self.assert_invalid("DUPLICATE_CASE_ID")

        second["id"] = "earlier"
        self.write_manifest()
        self.assert_invalid("CASES_UNSORTED")

    def test_missing_or_blank_provenance_and_license(self) -> None:
        for field, code in (("provenance", "INVALID_PROVENANCE"), ("license", "INVALID_LICENSE")):
            with self.subTest(field=field):
                original = self.case.pop(field)
                self.write_manifest()
                self.assert_invalid(code)
                self.case[field] = " "
                self.write_manifest()
                self.assert_invalid(code)
                self.case[field] = original

    def test_escaping_paths(self) -> None:
        paths = ("../audio.ulaw", "nested/../../audio.ulaw", "/audio.ulaw", "..\\audio.ulaw", "C:/audio.ulaw")
        for path in paths:
            with self.subTest(path=path):
                self.case["payloads"]["audio.ulaw"]["path"] = path
                self.write_manifest()
                self.assert_invalid("UNSAFE_PATH")

    def test_symlink_escaping_pack(self) -> None:
        audio = self.pack / "audio.ulaw"
        outside = audio.rename(self.temp / "audio.ulaw")
        audio.symlink_to(outside)
        self.assert_invalid("UNSAFE_PATH")

    def test_bad_hashes(self) -> None:
        audio = self.case["payloads"]["audio.ulaw"]
        audio["sha256"] = "wrong"
        self.write_manifest()
        self.assert_invalid("INVALID_HASH")
        audio["sha256"] = "0" * 64
        self.write_manifest()
        self.assert_invalid("HASH_MISMATCH")

    def test_audio_must_contain_whole_frames(self) -> None:
        self.write_payload("audio.ulaw", b"\xff" * 159)
        self.assert_invalid("INVALID_AUDIO_LENGTH")

    def test_duplicate_sequence_numbers(self) -> None:
        self.events[3]["sequence"] = 3
        self.write_payload("events.jsonl", canonical_jsonl(self.events))
        self.assert_invalid("DUPLICATE_SEQUENCE")

    def test_records_are_sorted_by_arrival_and_sequence(self) -> None:
        self.events[3], self.events[4] = self.events[4], self.events[3]
        self.write_payload("events.jsonl", canonical_jsonl(self.events))
        self.assert_invalid("EVENTS_UNSORTED")

    def test_noninteger_arrivals(self) -> None:
        for invalid in ("20", 20.0, True, -1):
            with self.subTest(arrival_ms=invalid):
                self.events[2]["arrival_ms"] = invalid
                self.write_payload("events.jsonl", canonical_jsonl(self.events))
                self.assert_invalid("INVALID_EVENT")

    def test_invalid_frame_references(self) -> None:
        for invalid in (-1, 2, True, "0", None):
            with self.subTest(frame_index=invalid):
                self.events[2]["media"]["frame_index"] = invalid
                self.write_payload("events.jsonl", canonical_jsonl(self.events))
                self.assert_invalid("INVALID_FRAME_REFERENCE")
        del self.events[2]["media"]
        self.write_payload("events.jsonl", canonical_jsonl(self.events))
        self.assert_invalid("INVALID_FRAME_REFERENCE")

    def test_malformed_answer_events(self) -> None:
        for answer in (
            [],
            [self.events[0], {**self.events[0], "sequence": 0}],
            [{**self.events[0], "arrival_ms": 1}],
            [{**self.events[0], "arrival_ms": 0.0}],
            [{**self.events[0], "arrival_ms": False}],
            [{**self.events[0], "sequence": 6}],
            [{"event": "call_answered", "arrival_ms": 0}],
            [{**self.events[0], "media": {"frame_index": 0}}],
        ):
            with self.subTest(answer=answer):
                self.write_payload("events.jsonl", canonical_jsonl(answer + self.events[1:]))
                self.assert_invalid("INVALID_ANSWER")

    def test_matched_pair_has_two_members(self) -> None:
        expected = json.loads((self.pack / "expected.json").read_bytes())
        expected["matched_pair"] = "pair-1"
        self.write_payload("expected.json", canonical_json(expected))
        self.assert_invalid("INVALID_MATCHED_PAIR")

        second = copy.deepcopy(self.case)
        second["id"] = "smoke-b"
        self.manifest["cases"].append(second)
        expected["answerer"] = "human"
        expected["verdict"] = "human"
        self.write_payload("expected.json", canonical_json(expected), case=second, directory="second")
        self.assertEqual(
            [case.expected.matched_pair for case in load_pack(self.pack)], ["pair-1", "pair-1"]
        )

        out = self.temp / "replay"
        replay(self.pack, out)
        for filename in ("result.json", "trace.json"):
            self.assertEqual((out / "smoke" / filename).read_bytes(), (out / "smoke-b" / filename).read_bytes())

    def test_canonical_bytes(self) -> None:
        record = {"quote": "¡Hola!", "arrival_ms": 0}
        self.assertEqual(canonical_json(record), b'{\n  "arrival_ms": 0,\n  "quote": "\xc2\xa1Hola!"\n}\n')
        self.assertEqual(canonical_jsonl([record]), b'{"arrival_ms":0,"quote":"\xc2\xa1Hola!"}\n')

        events = canonical_jsonl(self.events).replace(b"\n", b"\r\n")
        self.write_payload("events.jsonl", events)
        self.assert_invalid("NONCANONICAL_JSON")
        self.write_payload("events.jsonl", canonical_jsonl(self.events))
        (self.pack / "manifest.json").write_bytes(canonical_json(self.manifest).rstrip(b"\n"))
        self.assert_invalid("NONCANONICAL_JSON")

    def test_duplicate_json_keys(self) -> None:
        raw = canonical_json(self.manifest).replace(
            b'"pack_version": 1,', b'"pack_version": 2, "pack_version": 1,'
        )
        (self.pack / "manifest.json").write_bytes(raw)
        self.assert_invalid("INVALID_JSON")

    def test_commands_reject_invalid_pack_before_replay_writes(self) -> None:
        second = copy.deepcopy(self.case)
        second["id"] = "smoke-b"
        self.manifest["cases"].append(second)
        self.write_payload("expected.json", b"{}\n", case=second, directory="second")
        out = self.temp / "out"
        for command in (["validate"], ["replay", "--out", str(out)]):
            with self.subTest(command=command):
                run = subprocess.run(
                    [sys.executable, "-m", "whoanswered", *command, "--pack", str(self.pack)],
                    cwd=ROOT,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(run.returncode, 1)
                self.assertEqual(run.stderr, b"whoanswered: INVALID_EXPECTED: smoke-b/expected.json\n")
                self.assertFalse(out.exists())
