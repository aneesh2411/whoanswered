import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from whoanswered.fixtures import EVENT_TYPES, FRAME_BYTES, canonical_json, canonical_jsonl, load_pack
from whoanswered.replay import Observation, replay


SMOKE = Path(__file__).resolve().parents[1] / "fixtures" / "smoke"
UNCERTAIN = b'{\n  "at_ms": 2000,\n  "decision": "uncertain",\n  "provisional": true\n}\n'
HUMAN = b'{\n  "at_ms": 2000,\n  "decision": "human",\n  "provisional": true\n}\n'
DISCONNECTED = b'{\n  "at_ms": 2000,\n  "decision": "disconnect",\n  "provisional": true\n}\n'


def event(kind, at_ms, sequence, frame_index=None):
    record = {"event": kind, "arrival_ms": at_ms, "sequence": sequence}
    if frame_index is not None:
        record["media"] = {"frame_index": frame_index}
    return record


class RecordingDetector:
    def __init__(self, test, choose):
        self.test = test
        self.choose = choose
        self.seen = []
        self.records = []
        self.prefix_bytes = None

    def __call__(self, observation):
        # Pin the boundary where the detector receives data. Its only audio
        # input is one frame of bytes, and it receives no fixture context.
        self.test.assertIs(type(observation), Observation)
        record = asdict(observation)
        self.test.assertEqual(
            set(record), {"event", "at_ms", "sequence", "sample_start_ms", "audio"}
        )
        if observation.event == "media":
            self.test.assertIs(type(observation.audio), bytes)
            self.test.assertEqual(len(observation.audio), FRAME_BYTES)
            record["audio"] = observation.audio.hex()
        else:
            self.test.assertIsNone(observation.audio)
        self.seen.append(observation)
        self.records.append(record)
        if observation.event == "cutoff":
            self.prefix_bytes = canonical_json(self.records)
        return self.choose(observation)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_case(self, name, events, choose, *, audio=b"", label="silence"):
        pack = self.temp / name / "pack"
        out = self.temp / name / "out"
        payload_dir = pack / f"{name}-payloads"
        payload_dir.mkdir(parents=True)
        manifest = json.loads((SMOKE / "manifest.json").read_bytes())
        case = manifest["cases"][0]
        case["id"] = name
        for filename, data in (
            ("audio.ulaw", audio),
            ("events.jsonl", canonical_jsonl(events)),
            ("expected.json", canonical_json({
                "answerer": label, "matched_pair": None, "verdict": label, "actions": [],
            })),
        ):
            path = payload_dir / filename
            path.write_bytes(data)
            case["payloads"][filename] = {
                "path": path.relative_to(pack).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        (pack / "manifest.json").write_bytes(canonical_json(manifest))

        detector = RecordingDetector(self, choose)
        replay(pack, out, detector_factory=lambda: detector)
        return detector, (out / "result.json").read_bytes(), json.loads((out / "trace.json").read_bytes())

    def test_factory_creates_fresh_detectors_that_receive_only_observations(self):
        pack = SMOKE.parent / "v1"
        detectors = []

        def factory():
            detector = RecordingDetector(self, lambda observation: None)
            detectors.append(detector)
            return detector

        replay(pack, self.temp / "out", detector_factory=factory)

        for case, detector in zip(load_pack(pack), detectors, strict=True):
            with self.subTest(case_id=case.case_id):
                events = [observation.event for observation in detector.seen]
                self.assertEqual(events[:1], ["call_answered"])
                self.assertEqual(events.count("call_answered"), 1)
                self.assertEqual(events.count("cutoff"), 1)
                self.assertLessEqual(set(events), EVENT_TYPES | {"cutoff"})

    def test_cutoff_delivers_complete_batch_in_arrival_and_sequence_order(self):
        audio = b"".join(bytes([index]) * FRAME_BYTES for index in range(102))
        events = [
            event("call_answered", 0, 8),
            event("start", 0, 9),
            event("media", 1999, 30, 98),
            event("media", 1999, 31, 100),  # Samples after the cutoff, despite early arrival.
            event("media", 2000, 11, 99),  # Samples in [1980, 2000).
            event("stop", 2000, 12),
            event("media", 2000, 13, 101),
            event("media", 2001, 3, 97),  # Samples before the cutoff, despite late arrival.
            event("media", 2020, 4, 100),
        ]
        decisions = {11: "human", 12: "voicemail", 3: "voicemail"}
        detector, result, trace = self.run_case(
            "boundary", events, lambda obs: decisions.get(obs.sequence), audio=audio,
        )
        self.assertEqual(detector.seen, [
            Observation("call_answered", 0, 8),
            Observation("start", 0, 9),
            Observation("media", 1999, 30, 1960, bytes([98]) * FRAME_BYTES),
            Observation("media", 2000, 11, 1980, bytes([99]) * FRAME_BYTES),
            Observation("stop", 2000, 12),
            Observation("cutoff", 2000),
            Observation("media", 2001, 3, 1940, bytes([97]) * FRAME_BYTES),
            Observation("media", 2020, 4, 2000, bytes([100]) * FRAME_BYTES),
        ])
        self.assertEqual(result, HUMAN)
        self.assertEqual(trace[6]["verdict"], "voicemail")

    def test_late_frame_cannot_supply_missing_initial_commitment(self):
        events = [event("call_answered", 0, 1), event("media", 2001, 2, 0)]
        detector, result, trace = self.run_case(
            "late", events, lambda obs: "human" if obs.event == "media" else None,
            audio=b"\xff" * FRAME_BYTES,
        )
        self.assertEqual([obs.event for obs in detector.seen], ["call_answered", "cutoff", "media"])
        self.assertEqual(result, UNCERTAIN)
        self.assertEqual(trace[-1]["verdict"], "human")

    def test_detector_can_commit_on_cutoff(self):
        _, result, _ = self.run_case(
            "timer", [event("call_answered", 0, 1)],
            lambda obs: "human" if obs.event == "cutoff" else None,
        )
        self.assertEqual(result, HUMAN)

    def test_eof_and_stream_loss_do_not_confirm_call_completion(self):
        prefix = [event("call_answered", 0, 1), event("start", 0, 2)]
        for name, tail, expected_events, expected_result in (
            ("eof", [], ["cutoff"], UNCERTAIN),
            ("loss", [event("stop", 30, 3)], ["stop", "cutoff"], UNCERTAIN),
            ("completed", [event("call_completed", 2000, 3)], ["call_completed", "cutoff"], DISCONNECTED),
            ("completed-late", [event("call_completed", 2001, 3)], ["cutoff", "call_completed"], UNCERTAIN),
        ):
            with self.subTest(name=name):
                detector, result, _ = self.run_case(
                    name, prefix + tail,
                    lambda obs: "disconnect" if obs.event == "call_completed" else None,
                )
                self.assertEqual([obs.event for obs in detector.seen[2:]], expected_events)
                self.assertEqual(next(obs.at_ms for obs in detector.seen if obs.event == "cutoff"), 2000)
                self.assertEqual(result, expected_result)

    def test_replaced_tail_preserves_detector_prefix_and_result_bytes(self):
        common_audio = b"\xff" * (100 * FRAME_BYTES)
        common_events = [
            event("call_answered", 0, 1),
            event("connected", 0, 2),
            event("start", 0, 3),
            event("media", 20, 4, 0),
            event("media", 2000, 5, 99),
        ]

        def choose(observation):
            if observation.audio == b"\x01" * FRAME_BYTES:
                return "human"
            if observation.audio == b"\x7f" * FRAME_BYTES:
                return "voicemail"
            return None

        original, original_result, original_trace = self.run_case(
            "original", common_events + [event("media", 2020, 6, 100), event("stop", 2040, 7)],
            choose, audio=common_audio + b"\x01" * FRAME_BYTES, label="human",
        )
        replacement, replacement_result, replacement_trace = self.run_case(
            "replacement", common_events + [
                event("media", 2020, 8, 100), event("media", 2040, 9, 101),
                event("call_completed", 2060, 10),
            ],
            choose, audio=common_audio + b"\x7f" * (2 * FRAME_BYTES), label="voicemail",
        )

        self.assertIsInstance(original.prefix_bytes, bytes)
        self.assertEqual(original.prefix_bytes, replacement.prefix_bytes)
        self.assertEqual(original_result, replacement_result)
        self.assertEqual(original_result, UNCERTAIN)
        self.assertEqual(original_trace[6]["verdict"], "human")
        self.assertEqual(replacement_trace[6]["verdict"], "voicemail")


if __name__ == "__main__":
    unittest.main()
