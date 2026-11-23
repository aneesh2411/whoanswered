"""Score frozen verdicts and observed playback actions against fixture assertions."""

from dataclasses import asdict
from typing import TYPE_CHECKING

from .policy import PlaybackAction

if TYPE_CHECKING:
    from .replay import CaseReplay


def _rate(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _prefix(result: "CaseReplay") -> list[dict]:
    prefix = []
    for observation in result.observations:
        prefix.append({
            key: value for key, value in observation.items() if key not in ("reason", "verdict")
        })
        if observation["event"] == "cutoff":
            break
    return prefix


def build_scorecard(results: list["CaseReplay"]) -> dict:
    acoustic = [result for result in results if result.case.track == "acoustic"]
    committed = [result for result in acoustic if result.initial_verdict != "uncertain"]
    human = [result for result in results if result.case.expected.answerer == "human"]
    verdict_failures = [
        {
            "case_id": result.case.case_id,
            "expected": result.case.expected.verdict,
            "actual": result.initial_verdict,
        }
        for result in acoustic if result.initial_verdict != result.case.expected.verdict
    ]

    pairs = {}
    for result in results:
        if result.case.expected.matched_pair is not None:
            pairs.setdefault(result.case.expected.matched_pair, []).append(result)
    matched_prefix_assertions = []
    for pair, (left, right) in sorted(pairs.items()):
        identical_prefix = _prefix(left) == _prefix(right)
        verdicts = {result.case.case_id: result.initial_verdict for result in (left, right)}
        matched_prefix_assertions.append({
            "matched_pair": pair,
            "identical_prefix": identical_prefix,
            "initial_verdicts": verdicts,
            "passed": identical_prefix and all(verdict == "uncertain" for verdict in verdicts.values()),
        })

    policy_assertions = []
    for result in results:
        if result.case.track == "policy":
            expected = [
                PlaybackAction(action.action, action.at_ms) for action in result.case.expected.actions
            ]
            policy_assertions.append({
                "case_id": result.case.case_id,
                "expected_actions": [asdict(action) for action in expected],
                "actual_actions": [asdict(action) for action in result.actions],
                "passed": result.actions == expected,
            })

    return {
        "acoustic": {
            "committed_coverage": _rate(len(committed), len(acoustic)),
            "committed_error": _rate(
                sum(result.initial_verdict != result.case.expected.answerer for result in committed),
                len(committed),
            ),
            "verdict_assertions": {
                "passed": len(acoustic) - len(verdict_failures),
                "total": len(acoustic),
                "failures": verdict_failures,
            },
        },
        "wrong_voicemail_routing": _rate(
            sum(any(action.action == "play_voicemail" for action in result.actions) for result in human),
            len(human),
        ),
        "matched_prefix_assertions": matched_prefix_assertions,
        "policy_assertions": {
            "passed": sum(assertion["passed"] for assertion in policy_assertions),
            "total": len(policy_assertions),
            "cases": policy_assertions,
        },
    }
