"""Replay a validated local fixture pack."""

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .fixtures import FRAME_MS, ObservableInput, canonical_json, load_pack


CUTOFF_MS = 2_000


@dataclass(frozen=True, slots=True)
class Observation:
    """One detector input, with times relative to call answer."""

    event: str
    at_ms: int
    sequence: int | None = None
    sample_start_ms: int | None = None
    audio: bytes | None = None


# Return a verdict to commit, or None to keep listening.
Detector = Callable[[Observation], str | None]


def _abstain(_: Observation) -> None:
    return None


def replay(
    pack: Path, out: Path, *, detector_factory: Callable[[], Detector] | None = None
) -> None:
    """Replay a validated pack with a fresh detector for each case."""
    cases = load_pack(pack)
    for case in cases:
        case_out = out if len(cases) == 1 else out / case.case_id
        detector = _abstain if detector_factory is None else detector_factory()
        _replay_case(case.observable, case_out, detector)


def _observations(observable: ObservableInput) -> Iterator[Observation]:
    # Validation guarantees the answer marker is first and the event order is pinned.
    answer_ms = observable.events[0].arrival_ms
    cutoff_sent = False
    for event in observable.events:
        at_ms = event.arrival_ms - answer_ms
        if at_ms > CUTOFF_MS and not cutoff_sent:
            yield Observation("cutoff", CUTOFF_MS)
            cutoff_sent = True

        sample_start_ms = None
        audio = None
        if event.frame_index is not None:
            sample_start_ms = event.frame_index * FRAME_MS
            # A frame is indivisible. At or before the cutoff, discard frames
            # that claim to contain samples from beyond the decision window.
            if at_ms <= CUTOFF_MS and sample_start_ms + FRAME_MS > CUTOFF_MS:
                continue
            audio = observable.frame(event.frame_index)
        yield Observation(event.event, at_ms, event.sequence, sample_start_ms, audio)

    if not cutoff_sent:
        yield Observation("cutoff", CUTOFF_MS)


def _replay_case(observable: ObservableInput, out: Path, detector: Detector) -> None:
    first_verdict = None
    result = None
    trace = []
    for observation in _observations(observable):
        verdict = detector(observation)
        if result is None:
            if first_verdict is None:
                first_verdict = verdict
            if observation.event == "cutoff":
                result = {
                    "at_ms": observation.at_ms,
                    "decision": "uncertain" if first_verdict is None else first_verdict,
                    "provisional": True,
                }

        entry = {"at_ms": observation.at_ms, "event": observation.event}
        if observation.sequence is not None:
            entry["sequence"] = observation.sequence
        if observation.audio is not None:
            entry["sample_start_ms"] = observation.sample_start_ms
            entry["sha256"] = hashlib.sha256(observation.audio).hexdigest()
        if verdict is not None:
            entry["verdict"] = verdict
        trace.append(entry)

    out.mkdir(parents=True, exist_ok=True)
    for filename, payload in (("result.json", result), ("trace.json", trace)):
        (out / filename).write_bytes(canonical_json(payload))
