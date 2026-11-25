"""Replay a validated local fixture pack."""

import hashlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._detector import Decision, Detector as StreamingDetector, _V1Session
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
    text: str | None = None
    final: bool = False


type Detector = Callable[[Observation], str | DetectorUpdate | Decision | None]
type Scorecard = dict[str, Any]
PolicyRunner = Callable[[Iterable[PolicyEvent], InMemoryPlaybackSink], None]


@dataclass
class CaseReplay:
    case: FixtureCase
    initial_verdict: str
    observations: list[dict]
    actions: list[PlaybackAction] = field(default_factory=list)
    commitment_ms: int | None = None

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
    factory = AudioBaseline if detector_factory is None else detector_factory
    results = _replay_results(pack, factory, policy_runner)
    _write_replay(results, build_scorecard(results), out)


def evaluate(detector: StreamingDetector, pack_path: str | Path) -> Scorecard:
    """Score a fixture pack with a fresh session per case, without writing files."""
    return build_scorecard(_replay_results(Path(pack_path), lambda: _streaming_replay(detector), run_policy))


def replay_pack(detector: StreamingDetector, pack_path: str | Path, out: str | Path) -> Scorecard:
    """Replay a pack, write its scorecard and traces to ``out``, and return the scorecard."""
    results = _replay_results(Path(pack_path), lambda: _streaming_replay(detector), run_policy)
    scorecard = build_scorecard(results)
    _write_replay(results, scorecard, Path(out))
    return scorecard


def _streaming_replay(detector: StreamingDetector) -> Detector:
    session = detector.start()
    if isinstance(session, _V1Session):
        return session._observe

    feed_audio_at = getattr(session, "_feed_audio_at", None)
    feed_transcript = getattr(session, "feed_transcript", None)

    def observe(observation: Observation) -> Decision | None:
        if observation.audio is not None:
            if feed_audio_at is not None:
                return feed_audio_at(
                    observation.audio, t_ms=observation.at_ms, sample_start_ms=observation.sample_start_ms,
                )
            return session.feed_audio(observation.audio, t_ms=observation.at_ms)
        if observation.text is not None:
            if feed_transcript is not None:
                return feed_transcript(observation.text, t_ms=observation.at_ms, final=observation.final)
            return None
        update = session.feed_event(observation.event, t_ms=observation.at_ms)
        if observation.event == "cutoff" and update is None:
            return session.decision()
        return update

    return observe


def _replay_results(pack: Path, factory: Callable[[], Detector], policy_runner: PolicyRunner) -> list[CaseReplay]:
    results = []
    for case in load_pack(pack):
        result = replay_case(case, factory())
        if case.track == "policy":
            result.actions = replay_policy(case, policy_runner)
        results.append(result)
    return results


def _write_replay(results: list[CaseReplay], scorecard: Scorecard, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "scorecard.json").write_bytes(canonical_json(scorecard))
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


def replay_policy(case: FixtureCase, policy_runner: PolicyRunner) -> list[PlaybackAction]:
    sink = InMemoryPlaybackSink()
    policy_runner(case.policy_events, sink)
    return sink.trace


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
        yield Observation(event.event, at_ms, event.sequence, sample_start_ms, audio, event.text, event.final)

    if not cutoff_sent:
        yield Observation("cutoff", CUTOFF_MS)


def replay_case(case: FixtureCase, detector: Detector, *, cutoff_only: bool = False) -> CaseReplay:
    """Run the detector on observable inputs and record when its initial verdict was made."""
    commitment_ms = None
    initial_verdict = None
    trace = []
    for observation in _observations(case.observable):
        update = detector(observation)
        decision_ms = observation.at_ms
        if isinstance(update, Decision):
            verdict = update.label if update.committed else None
            if not update.committed and observation.event == "cutoff":
                verdict = "uncertain"
            reason = update.reason
            decision_ms = update.at_ms
        elif isinstance(update, DetectorUpdate):
            verdict, reason = update.verdict, update.reason
        else:
            verdict, reason = update, None
        if initial_verdict is None and verdict is not None:
            initial_verdict = verdict
            if verdict != "uncertain":
                commitment_ms = decision_ms

        entry = {"at_ms": observation.at_ms, "event": observation.event}
        if observation.sequence is not None:
            entry["sequence"] = observation.sequence
        if observation.audio is not None:
            entry["sample_start_ms"] = observation.sample_start_ms
            entry["sha256"] = hashlib.sha256(observation.audio).hexdigest()
        if observation.text is not None:
            entry["text"] = observation.text
            entry["final"] = observation.final
        if reason is not None:
            entry["reason"] = reason
        if verdict is not None:
            entry["verdict"] = verdict
        trace.append(entry)
        if observation.event == "cutoff":
            if initial_verdict is None:
                initial_verdict = "uncertain"
            if cutoff_only:
                break

    assert initial_verdict is not None
    return CaseReplay(case, initial_verdict, trace, commitment_ms=commitment_ms)
