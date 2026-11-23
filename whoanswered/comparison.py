"""Compare bundled detectors on the frozen revision 2 evaluation cases."""

from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from statistics import median

from .fixtures import CUTOFF_MS, FixtureValidationError, canonical_json, load_pack
from .policy import run_policy
from .replay import CaseReplay, Detector, PolicyRunner, replay_case, replay_policy
from .scorecard import matched_prefix_assertions, metric_rate, policy_assertions


ANSWERERS = ("human", "voicemail")


def _acoustic_metrics(results: list[CaseReplay]) -> dict:
    commitments = [result for result in results if result.commitment_ms is not None]
    correct = sum(result.initial_verdict == result.case.expected.answerer for result in commitments)
    directions = Counter((result.case.expected.answerer, result.initial_verdict) for result in results)
    by_class = Counter(result.case.expected.answerer for result in results)
    by_family = Counter(result.case.family or "unspecified" for result in results)
    family_abstentions = Counter(
        result.case.family or "unspecified" for result in results if result.commitment_ms is None
    )
    times = [result.commitment_ms for result in results if result.commitment_ms is not None]
    return {
        "correct_over_all_eligible": metric_rate(correct, len(results)),
        "committed_coverage": metric_rate(len(commitments), len(results)),
        "committed_error": metric_rate(len(commitments) - correct, len(commitments)),
        "human_as_mailbox": metric_rate(directions["human", "voicemail"], by_class["human"]),
        "mailbox_as_human": metric_rate(directions["voicemail", "human"], by_class["voicemail"]),
        "abstentions_by_class": {
            answerer: metric_rate(directions[answerer, "uncertain"], by_class[answerer])
            for answerer in ANSWERERS
        },
        "abstentions_by_family": {
            family: metric_rate(family_abstentions[family], total) for family, total in by_family.items()
        },
        "median_commitment_ms": median(times) if times else None,
    }


def _improves_on_v1(candidate: dict, v1: dict) -> bool:
    changes = {
        metric: candidate[metric]["numerator"] - v1[metric]["numerator"]
        for metric in (
            "committed_coverage", "human_as_mailbox", "mailbox_as_human",
            "correct_over_all_eligible", "committed_error",
        )
    }
    return (
        changes["committed_coverage"] >= 0
        and changes["human_as_mailbox"] <= 0
        and changes["mailbox_as_human"] <= 0
        and (changes["correct_over_all_eligible"] > 0 or changes["committed_error"] < 0)
    )


def compare(
    pack: Path,
    out: Path,
    *,
    detector_factories: Mapping[str, Callable[[], Detector]],
    policy_runner: PolicyRunner = run_policy,
) -> None:
    """Write one canonical JSON comparison report for the reserved split."""
    cases = [case for case in load_pack(pack) if case.split == "eval"]
    if not cases:
        raise FixtureValidationError("MISSING_EVAL_SPLIT", "manifest.json")
    eligible = [
        case for case in cases
        if case.track == "acoustic" and case.expected.matched_pair is None
        and case.expected.answerer in ANSWERERS
    ]
    matched = [case for case in cases if case.expected.matched_pair is not None]
    policy = policy_assertions(
        (case, replay_policy(case, policy_runner)) for case in cases if case.track == "policy"
    )

    acoustic = {}
    prefixes = {}
    for name, factory in detector_factories.items():
        acoustic[name] = _acoustic_metrics([
            replay_case(case, factory(), cutoff_only=True) for case in eligible
        ])
        prefixes[name] = matched_prefix_assertions(
            replay_case(case, factory(), cutoff_only=True) for case in matched
        )
    v1 = acoustic["v1"]
    promotion = {
        name: (
            policy["passed"] == policy["total"]
            and all(assertion["passed"] for assertion in prefixes[name])
            and _improves_on_v1(metrics, v1)
        )
        for name, metrics in acoustic.items() if name != "v1"
    }

    report = {
        "split": "eval",
        "cutoff_ms": CUTOFF_MS,
        "eligible_case_ids": [case.case_id for case in eligible],
        "acoustic": acoustic,
        "matched_prefix_assertions": prefixes,
        "policy_assertions": policy,
        "promotion": promotion,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_bytes(canonical_json(report))
