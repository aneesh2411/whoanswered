"""Read and validate local fixture packs."""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .policy import PolicyEvent


SCHEMA_VERSION = 1
PACK_VERSION = 1  # Frozen revision written by the public v1 generator.
PACK_VERSIONS = (PACK_VERSION, 2)
FRAME_BYTES = 160
FRAME_MS = 20
CUTOFF_MS = 2_000
SILENT_FRAME = b"\xff" * FRAME_BYTES
PAYLOAD_NAMES = ("audio.ulaw", "events.jsonl", "expected.json")
CASE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
EVENT_TYPES = {"call_answered", "connected", "start", "media", "stop", "call_completed"}


class FixtureValidationError(ValueError):
    """A pack failed validation with a stable, machine-readable code."""

    def __init__(self, code: str, location: str) -> None:
        self.code = code
        super().__init__(f"{code}: {location}")


@dataclass(frozen=True)
class Event:
    event: str
    arrival_ms: int
    sequence: int
    frame_index: int | None = None


@dataclass(frozen=True)
class ObservableInput:
    audio: bytes
    events: tuple[Event, ...]

    def frame(self, index: int) -> bytes:
        if not _nonnegative_int(index) or index >= len(self.audio) // FRAME_BYTES:
            raise IndexError(index)
        start = index * FRAME_BYTES
        return self.audio[start : start + FRAME_BYTES]


@dataclass(frozen=True)
class ActionAssertion:
    action: str
    at_ms: int


@dataclass(frozen=True)
class EvaluatorAssertions:
    answerer: str
    matched_pair: str | None
    verdict: str
    actions: tuple[ActionAssertion, ...]


@dataclass(frozen=True)
class FixtureCase:
    case_id: str
    track: str
    source_group: str
    provenance: str
    license: str
    observable: ObservableInput
    expected: EvaluatorAssertions
    policy_events: tuple[PolicyEvent, ...] = ()
    split: str | None = None
    family: str | None = None


def frame_ends_by_cutoff(frame_index: int) -> bool:
    return (frame_index + 1) * FRAME_MS <= CUTOFF_MS


def canonical_json(value: object) -> bytes:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def canonical_jsonl(records: list[dict]) -> bytes:
    return "".join(
        json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for record in records
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _read_object(raw: bytes, location: str) -> dict:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError) as error:
        raise FixtureValidationError("INVALID_JSON", location) from error
    if not isinstance(value, dict):
        raise FixtureValidationError("INVALID_OBJECT", location)
    return value


def _check_canonical(raw: bytes, value: dict | list[dict], location: str) -> None:
    try:
        encoded = canonical_jsonl(value) if isinstance(value, list) else canonical_json(value)
    except (ValueError, RecursionError) as error:
        raise FixtureValidationError("INVALID_JSON", location) from error
    if raw != encoded:
        raise FixtureValidationError("NONCANONICAL_JSON", location)


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _pack_file(root: Path, name: str) -> bytes:
    parts = name.split("/")
    if (any(part in {"", ".", ".."} for part in parts)
            or any(char in name for char in "\\:\0")):
        raise FixtureValidationError("UNSAFE_PATH", name)
    try:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise FixtureValidationError("UNSAFE_PATH", name)
        if path.is_file():
            return path.read_bytes()
    except (OSError, RuntimeError, UnicodeError) as error:
        raise FixtureValidationError("FILE_UNREADABLE", name) from error
    raise FixtureValidationError("FILE_UNREADABLE", name)


def _read_payload(root: Path, payload: object, filename: str, location: str) -> bytes:
    if not isinstance(payload, dict) or payload.keys() != {"path", "sha256"}:
        raise FixtureValidationError("INVALID_PAYLOAD", location)
    name = payload["path"]
    digest = payload["sha256"]
    if not _nonblank(name):
        raise FixtureValidationError("INVALID_PAYLOAD", location)
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise FixtureValidationError("INVALID_HASH", location)
    raw = _pack_file(root, name)
    if name.rsplit("/", 1)[-1] != filename:
        raise FixtureValidationError("INVALID_PAYLOAD", location)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise FixtureValidationError("HASH_MISMATCH", location)
    return raw


def _read_events(raw: bytes, frame_count: int, location: str) -> tuple[Event, ...]:
    fields = {"event", "arrival_ms", "sequence"}
    records = []
    events = []
    sequences = set()
    answer_count = 0
    for number, data in enumerate(raw.splitlines(), 1):
        line = f"{location}:{number}"
        record = _read_object(data, line)
        records.append(record)
        kind = record.get("event")
        arrival = record.get("arrival_ms")
        sequence = record.get("sequence")
        valid_times = _nonnegative_int(arrival) and _nonnegative_int(sequence)
        if kind == "call_answered":
            answer_count += 1
            if record.keys() != fields or not valid_times or arrival != 0:
                raise FixtureValidationError("INVALID_ANSWER", line)
        if not isinstance(kind, str) or kind not in EVENT_TYPES or not valid_times:
            raise FixtureValidationError("INVALID_EVENT", line)
        if sequence in sequences:
            raise FixtureValidationError("DUPLICATE_SEQUENCE", line)
        sequences.add(sequence)

        frame_index = None
        if kind == "media":
            media = record.get("media")
            if (record.keys() != fields | {"media"} or not isinstance(media, dict)
                    or media.keys() != {"frame_index"}):
                raise FixtureValidationError("INVALID_FRAME_REFERENCE", line)
            frame_index = media["frame_index"]
            if not _nonnegative_int(frame_index) or frame_index >= frame_count:
                raise FixtureValidationError("INVALID_FRAME_REFERENCE", line)
        elif record.keys() != fields:
            raise FixtureValidationError("INVALID_EVENT", line)
        events.append(Event(kind, arrival, sequence, frame_index))

    ordered = sorted(events, key=lambda event: (event.arrival_ms, event.sequence))
    if answer_count != 1 or events[0].event != "call_answered" or ordered[0].event != "call_answered":
        raise FixtureValidationError("INVALID_ANSWER", location)
    if events != ordered:
        raise FixtureValidationError("EVENTS_UNSORTED", location)
    _check_canonical(raw, records, location)
    return tuple(events)


def _read_expected(raw: bytes, location: str) -> EvaluatorAssertions:
    record = _read_object(raw, location)
    if record.keys() != {"answerer", "matched_pair", "verdict", "actions"}:
        raise FixtureValidationError("INVALID_EXPECTED", location)
    answerer = record["answerer"]
    pair = record["matched_pair"]
    verdict = record["verdict"]
    actions = record["actions"]
    if (not _nonblank(answerer) or not _nonblank(verdict)
            or (pair is not None and not _nonblank(pair)) or not isinstance(actions, list)):
        raise FixtureValidationError("INVALID_EXPECTED", location)
    assertions = []
    for action in actions:
        if not isinstance(action, dict) or action.keys() != {"action", "at_ms"}:
            raise FixtureValidationError("INVALID_EXPECTED", location)
        label, time = action["action"], action["at_ms"]
        if (not _nonblank(label) or not _nonnegative_int(time)
                or (assertions and time < assertions[-1].at_ms)):
            raise FixtureValidationError("INVALID_EXPECTED", location)
        assertions.append(ActionAssertion(label, time))
    _check_canonical(raw, record, location)
    return EvaluatorAssertions(answerer, pair, verdict, tuple(assertions))


def _read_policy_events(entry: dict) -> tuple[PolicyEvent, ...]:
    if "policy_events" not in entry:
        return ()
    location = f"{entry['id']}/policy_events"
    records = entry["policy_events"]
    if entry["track"] != "policy" or not isinstance(records, list):
        raise FixtureValidationError("INVALID_POLICY_EVENTS", location)

    events = []
    for index, record in enumerate(records):
        if (not isinstance(record, dict) or record.keys() != {"event", "at_ms"}
                or record["event"] not in (
                    "screener", "prompt_end", "human_takeover", "mailbox_ready", "disconnect"
                )
                or not _nonnegative_int(record["at_ms"])):
            raise FixtureValidationError("INVALID_POLICY_EVENT", f"{location}/{index}")
        events.append(PolicyEvent(record["event"], record["at_ms"]))
    return tuple(events)


def _read_case(root: Path, entry: dict, pack_version: int) -> FixtureCase:
    case_id = entry["id"]
    for field, code in (
        ("track", "INVALID_TRACK"),
        ("source_group", "INVALID_SOURCE_GROUP"),
        ("provenance", "INVALID_PROVENANCE"),
        ("license", "INVALID_LICENSE"),
    ):
        if not _nonblank(entry.get(field)):
            raise FixtureValidationError(code, case_id)
    split = family = None
    if pack_version == 2:
        split = entry.get("split")
        if split not in ("dev", "eval"):
            raise FixtureValidationError("INVALID_SPLIT", case_id)
        if "family" in entry:
            family = entry["family"]
            if not _nonblank(family):
                raise FixtureValidationError("INVALID_FAMILY", case_id)
    payloads = entry.get("payloads")
    if not isinstance(payloads, dict) or payloads.keys() != set(PAYLOAD_NAMES):
        raise FixtureValidationError("INVALID_PAYLOADS", case_id)

    audio, event_data, expected_data = (
        _read_payload(root, payloads[name], name, f"{case_id}/{name}") for name in PAYLOAD_NAMES
    )
    frame_count, remainder = divmod(len(audio), FRAME_BYTES)
    if remainder:
        raise FixtureValidationError("INVALID_AUDIO_LENGTH", f"{case_id}/audio.ulaw")
    events = _read_events(event_data, frame_count, f"{case_id}/events.jsonl")
    expected = _read_expected(expected_data, f"{case_id}/expected.json")
    return FixtureCase(
        case_id, entry["track"], entry["source_group"], entry["provenance"], entry["license"],
        ObservableInput(audio, events), expected, _read_policy_events(entry), split, family,
    )


def _decision_frames(observable: ObservableInput) -> Iterator[bytes]:
    for event in observable.events:
        if event.arrival_ms > CUTOFF_MS:
            break
        index = event.frame_index
        if index is not None and frame_ends_by_cutoff(index):
            yield observable.frame(index)


def _opening_frame(frames: Iterable[bytes]) -> bytes | None:
    for frame in frames:
        # Both G.711 mu-law zero codes represent the same silence.
        frame = frame.replace(b"\x7f", b"\xff")
        if frame != SILENT_FRAME:
            return frame
    return None


def validate_split_boundary(cases: Iterable[FixtureCase]) -> None:
    """Reject leakage between dev and eval, including across validated packs."""
    split_cases = [case for case in cases if case.split is not None]
    if len({case.split for case in split_cases}) < 2:
        return

    sources: dict[str, str | None] = {}
    pairs: dict[str, str | None] = {}
    for case in split_cases:
        if sources.setdefault(case.source_group, case.split) != case.split:
            raise FixtureValidationError("SPLIT_SOURCE_GROUP", case.source_group)
        pair = case.expected.matched_pair
        if pair is not None and pairs.setdefault(pair, case.split) != case.split:
            raise FixtureValidationError("SPLIT_MATCHED_PAIR", pair)

    openings: dict[bytes, FixtureCase] = {}
    for case in split_cases:
        audio = case.observable.audio
        stored_frames = (
            audio[start : start + FRAME_BYTES] for start in range(0, len(audio), FRAME_BYTES)
        )
        case_openings = (_opening_frame(stored_frames), _opening_frame(_decision_frames(case.observable)))
        for opening in case_openings:
            if opening is not None:
                other = openings.setdefault(opening, case)
                if other.split != case.split:
                    location = "/".join(sorted((other.case_id, case.case_id)))
                    raise FixtureValidationError("SPLIT_AUDIO_PREFIX", location)


def load_pack(pack: Path, *, dev_only: bool = False) -> tuple[FixtureCase, ...]:
    """Validate the whole pack and return cases in manifest order."""
    try:
        root = pack.resolve()
    except (OSError, RuntimeError) as error:
        raise FixtureValidationError("FILE_UNREADABLE", "manifest.json") from error
    raw = _pack_file(root, "manifest.json")
    manifest = _read_object(raw, "manifest.json")
    for field, versions, code in (
        ("schema_version", (SCHEMA_VERSION,), "UNSUPPORTED_SCHEMA_VERSION"),
        ("pack_version", PACK_VERSIONS, "UNSUPPORTED_PACK_VERSION"),
    ):
        value = manifest.get(field)
        if type(value) is not int or value not in versions:
            raise FixtureValidationError(code, "manifest.json")
    entries = manifest.get("cases")
    if not isinstance(entries, list) or not entries:
        raise FixtureValidationError("INVALID_CASES", "manifest.json")
    case_ids = []
    seen = set()
    for index, entry in enumerate(entries):
        case_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id):
            raise FixtureValidationError("INVALID_CASE_ID", f"manifest.json/cases/{index}")
        # Case IDs also serve as output directory names on case-insensitive filesystems.
        if case_id.lower() in seen:
            raise FixtureValidationError("DUPLICATE_CASE_ID", case_id)
        seen.add(case_id.lower())
        case_ids.append(case_id)
    if case_ids != sorted(case_ids):
        raise FixtureValidationError("CASES_UNSORTED", "manifest.json")

    # Tuning must reject mixed or eval packs before opening any case payload.
    if dev_only and (
        manifest["pack_version"] != 2 or any(entry.get("split") != "dev" for entry in entries)
    ):
        raise FixtureValidationError("DEV_SPLIT_REQUIRED", "manifest.json")

    cases = tuple(_read_case(root, entry, manifest["pack_version"]) for entry in entries)
    pair_counts = Counter(
        case.expected.matched_pair for case in cases if case.expected.matched_pair is not None
    )
    for pair, count in pair_counts.items():
        if count != 2:
            raise FixtureValidationError("INVALID_MATCHED_PAIR", pair)
    _check_canonical(raw, manifest, "manifest.json")
    validate_split_boundary(cases)
    return cases
