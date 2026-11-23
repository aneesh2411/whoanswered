"""Score frozen verdicts and observed playback actions against fixture assertions."""

from collections.abc import Iterable
from dataclasses import asdict
from typing import TYPE_CHECKING

from .policy import PlaybackAction

if TYPE_CHECKING:
    from .fixtures import FixtureCase
    from .replay import CaseReplay


def metric_rate(numerator: int, denominator: int) -> dict:
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


def matched_prefix_assertions(results: Iterable["CaseReplay"]) -> list[dict]:
    pairs = {}
    for result in results:
        if result.case.expected.matched_pair is not None:
            pairs.setdefault(result.case.expected.matched_pair, []).append(result)
    assertions = []
    for pair, (left, right) in sorted(pairs.items()):
        identical_prefix = _prefix(left) == _prefix(right)
        verdicts = {result.case.case_id: result.initial_verdict for result in (left, right)}
        assertions.append({
            "matched_pair": pair,
            "identical_prefix": identical_prefix,
            "initial_verdicts": verdicts,
            "passed": identical_prefix and all(verdict == "uncertain" for verdict in verdicts.values()),
        })
    return assertions


def policy_assertions(results: Iterable[tuple["FixtureCase", list[PlaybackAction]]]) -> dict:
    assertions = []
    for case, actions in results:
        expected = [PlaybackAction(action.action, action.at_ms) for action in case.expected.actions]
        assertions.append({
            "case_id": case.case_id,
            "expected_actions": [asdict(action) for action in expected],
            "actual_actions": [asdict(action) for action in actions],
            "passed": actions == expected,
        })
    return {
        "passed": sum(assertion["passed"] for assertion in assertions),
        "total": len(assertions),
        "cases": assertions,
    }


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
    return {
        "acoustic": {
            "committed_coverage": metric_rate(len(committed), len(acoustic)),
            "committed_error": metric_rate(
                sum(result.initial_verdict != result.case.expected.answerer for result in committed),
                len(committed),
            ),
            "verdict_assertions": {
                "passed": len(acoustic) - len(verdict_failures),
                "total": len(acoustic),
                "failures": verdict_failures,
            },
        },
        "wrong_voicemail_routing": metric_rate(
            sum(any(action.action == "play_voicemail" for action in result.actions) for result in human),
            len(human),
        ),
        "matched_prefix_assertions": matched_prefix_assertions(results),
        "policy_assertions": policy_assertions(
            (result.case, result.actions) for result in results if result.case.track == "policy"
        ),
    }
