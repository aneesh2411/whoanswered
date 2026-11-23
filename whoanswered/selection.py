"""Compare the reference and cutoff detectors on development cases."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

from .baseline import ACTIVE_MEAN_SQUARE, HUMAN_MAX_MS, HUMAN_MIN_MS, HUMAN_PAUSE_MS, AudioBaseline
from .comparison import acoustic_metrics, eligible_acoustic_cases
from .fixtures import CUTOFF_MS, FRAME_MS, FixtureValidationError, canonical_json, load_pack
from .replay import CaseReplay, Detector, replay_case
from .scorecard import matched_prefix_assertions
from .veto import CutoffVeto, EvidenceScore


SCORE_TRIAL_THRESHOLDS = (6, 12, 24)


def _outcome(result: CaseReplay) -> str:
    if result.commitment_ms is None:
        return "abstained"
    return "correct" if result.initial_verdict == result.case.expected.answerer else "wrong"


def _family_changes(reference: list[CaseReplay], candidate: list[CaseReplay]) -> dict:
    changes = {}
    for prior, current in zip(reference, candidate, strict=True):
        counts = changes.setdefault(prior.case.family or "unspecified", {
            "eligible_cases": 0,
            "lost_correct_commitments": 0,
            "avoided_errors": 0,
            "gained_correct_commitments": 0,
            "introduced_errors": 0,
        })
        before, after = _outcome(prior), _outcome(current)
        counts["eligible_cases"] += 1
        counts["lost_correct_commitments"] += before == "correct" and after != "correct"
        counts["avoided_errors"] += before == "wrong" and after != "wrong"
        counts["gained_correct_commitments"] += before != "correct" and after == "correct"
        counts["introduced_errors"] += before != "wrong" and after == "wrong"
    return changes


def tune(pack: Path, out: Path) -> None:
    """Write the canonical dev trade-off and selected operating point."""
    cases = load_pack(pack, dev_only=True)
    eligible = eligible_acoustic_cases(cases)
    if not eligible:
        raise FixtureValidationError("NO_ELIGIBLE_DEV_CASES", "manifest.json")
    matched = [case for case in cases if case.expected.matched_pair is not None]

    def run(factory: Callable[[], Detector]) -> tuple[list[CaseReplay], list[dict]]:
        results = [replay_case(case, factory(), cutoff_only=True) for case in eligible]
        prefixes = matched_prefix_assertions(
            replay_case(case, factory(), cutoff_only=True) for case in matched
        )
        return results, prefixes

    baseline, baseline_prefixes = run(AudioBaseline)
    veto, veto_prefixes = run(CutoffVeto)
    veto_tradeoff = _family_changes(baseline, veto)
    lost_to_resumption = any(
        _outcome(prior) == "correct" and current.observations[-1].get("reason") == "resumed_speech"
        for prior, current in zip(baseline, veto, strict=True)
    )
    thresholds = SCORE_TRIAL_THRESHOLDS if lost_to_resumption else ()
    trials = []
    qualifying = []
    for threshold in sorted(thresholds):
        results, prefixes = run(partial(EvidenceScore, threshold))
        tradeoff = _family_changes(veto, results)
        trials.append({
            "threshold_frames": threshold,
            "acoustic": acoustic_metrics(results),
            "matched_prefix_assertions": prefixes,
            "vs_veto_by_family": tradeoff,
        })
        gains = sum(counts["gained_correct_commitments"] for counts in tradeoff.values())
        if (
            gains
            and all(assertion["passed"] for assertion in prefixes)
            and all(
                not (counts["lost_correct_commitments"] or counts["introduced_errors"])
                for counts in tradeoff.values()
            )
        ):
            # Keep the stricter threshold when candidates gain the same coverage.
            qualifying.append((gains, threshold))

    threshold = max(qualifying)[1] if qualifying else None
    report = {
        "split": "dev",
        "cutoff_ms": CUTOFF_MS,
        "eligible_case_ids": [case.case_id for case in eligible],
        "acoustic": {"v1": acoustic_metrics(baseline), "veto": acoustic_metrics(veto)},
        "matched_prefix_assertions": {"v1": baseline_prefixes, "veto": veto_prefixes},
        "veto_vs_v1_by_family": veto_tradeoff,
        "score_trials": trials,
        "selection": {
            "detector": "veto" if threshold is None else "score",
            "config": {
                "active_mean_square": ACTIVE_MEAN_SQUARE,
                "cutoff_ms": CUTOFF_MS,
                "frame_ms": FRAME_MS,
                "greeting_max_ms": HUMAN_MAX_MS,
                "greeting_min_ms": HUMAN_MIN_MS,
                "opening_pause_min_ms": HUMAN_PAUSE_MS,
            },
            "threshold_frames": threshold,
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "selection.json").write_bytes(canonical_json(report))
