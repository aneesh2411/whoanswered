"""Replay a validated local fixture pack."""

import hashlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .baseline import AudioBaseline, DetectorUpdate
from .fixtures import (
    CUTOFF_MS,
    FRAME_MS,
    FixtureCase,
    ObservableInput,
    canonical_json,
    canonical_jsonl,
    frame_ends_by_cutoff,
    load_pack,
)
from .policy import InMemoryPlaybackSink, PlaybackAction, PolicyEvent, run_policy
from .scorecard import build_scorecard


@dataclass(frozen=True, slots=True)
class Observation:
    """One detector input, with times relative to call answer."""

    event: str
    at_ms: int
    sequence: int | None = None
    sample_start_ms: int | None = None
    audio: bytes | None = None


# A detector may return a verdict, a trace reason with an optional verdict, or None.
Detector = Callable[[Observation], str | DetectorUpdate | None]
PolicyRunner = Callable[[Iterable[PolicyEvent], InMemoryPlaybackSink], None]


@dataclass
class CaseReplay:
    case: FixtureCase
    initial_verdict: str
    observations: list[dict]
    actions: list[PlaybackAction]

    def initial_decision(self) -> dict:
        return {"at_ms": CUTOFF_MS, "decision": self.initial_verdict, "provisional": True}


def replay(
    pack: Path,
    out: Path,
    *,
    detector_factory: Callable[[], Detector] | None = None,
    policy_runner: PolicyRunner = run_policy,
) -> None:
    """Replay a validated pack with a fresh detector and playback sink for each case."""
    cases = load_pack(pack)
    factory = AudioBaseline if detector_factory is None else detector_factory
    results = []
    for case in cases:
        initial_verdict, observations = _run_detector(case.observable, factory())
        sink = InMemoryPlaybackSink()
        if case.track == "policy":
            policy_runner(case.policy_events, sink)
        results.append(CaseReplay(case, initial_verdict, observations, sink.trace))

    out.mkdir(parents=True, exist_ok=True)
    (out / "scorecard.json").write_bytes(canonical_json(build_scorecard(results)))
    (out / "traces.jsonl").write_bytes(canonical_jsonl([
        {
            "case_id": result.case.case_id,
            "track": result.case.track,
            "initial_decision": result.initial_decision(),
            "observations": result.observations,
            "policy_events": [asdict(event) for event in result.case.policy_events],
            "actions": [asdict(action) for action in result.actions],
        }
        for result in results
    ]))
    for result in results:
        case_out = out if len(results) == 1 else out / result.case.case_id
        case_out.mkdir(parents=True, exist_ok=True)
        (case_out / "result.json").write_bytes(canonical_json(result.initial_decision()))
        (case_out / "trace.json").write_bytes(canonical_json(result.observations))


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
            if at_ms <= CUTOFF_MS and not frame_ends_by_cutoff(event.frame_index):
                continue
            audio = observable.frame(event.frame_index)
        yield Observation(event.event, at_ms, event.sequence, sample_start_ms, audio)

    if not cutoff_sent:
        yield Observation("cutoff", CUTOFF_MS)


def _run_detector(observable: ObservableInput, detector: Detector) -> tuple[str, list[dict]]:
    first_verdict = None
    initial_verdict = None
    trace = []
    for observation in _observations(observable):
        update = detector(observation)
        if isinstance(update, DetectorUpdate):
            verdict, reason = update.verdict, update.reason
        else:
            verdict, reason = update, None
        if initial_verdict is None:
            if first_verdict is None:
                first_verdict = verdict
            if observation.event == "cutoff":
                initial_verdict = "uncertain" if first_verdict is None else first_verdict

        entry = {"at_ms": observation.at_ms, "event": observation.event}
        if observation.sequence is not None:
            entry["sequence"] = observation.sequence
        if observation.audio is not None:
            entry["sample_start_ms"] = observation.sample_start_ms
            entry["sha256"] = hashlib.sha256(observation.audio).hexdigest()
        if reason is not None:
            entry["reason"] = reason
        if verdict is not None:
            entry["verdict"] = verdict
        trace.append(entry)

    assert initial_verdict is not None
    return initial_verdict, trace
