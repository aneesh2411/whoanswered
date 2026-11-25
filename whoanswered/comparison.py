"""Compare bundled detectors on reserved evaluation cases."""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from statistics import median

from . import _detector, baseline, signals, veto
from .fixtures import (
    CUTOFF_MS, FRAME_MS, SIGNAL_PACK_VERSION, FixtureCase, FixtureValidationError, canonical_json, load_pack,
)
from .policy import run_policy
from .replay import CaseReplay, Detector, PolicyRunner, _streaming_replay, replay_case, replay_policy
from .scorecard import matched_prefix_assertions, metric_rate, policy_assertions


ANSWERERS = ("human", "voicemail")
_V2_FACTORIES = {
    "v1": baseline.AudioBaseline,
    "veto": veto.CutoffVeto,
    "score": veto.EvidenceScore,
}


_V3_FACTORIES = {
    "v1": baseline.AudioBaseline,
    "transcript": lambda: _streaming_replay(signals.TranscriptCueDetector()),
    "tone": lambda: _streaming_replay(signals.ToneDetector()),
    "fusion": lambda: _streaming_replay(signals.FusionDetector()),
}
DETECTOR_FACTORIES = _V2_FACTORIES | _V3_FACTORIES


def _source_digest(path: Path) -> str:
    return sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _bundled_detector_configs() -> dict:
    baseline_version = {"baseline.py": _source_digest(Path(baseline.__file__))}
    cutoff_version = baseline_version | {"veto.py": _source_digest(Path(veto.__file__))}
    greeting_thresholds = {
        "active_mean_square": baseline.ACTIVE_MEAN_SQUARE,
        "greeting_min_ms": baseline.HUMAN_MIN_MS,
        "greeting_max_ms": baseline.HUMAN_MAX_MS,
        "opening_pause_min_ms": baseline.HUMAN_PAUSE_MS,
    }
    return {
        "v1": {
            "version": baseline_version,
            "thresholds": greeting_thresholds | {"mailbox_min_ms": baseline.VOICEMAIL_MIN_MS},
        },
        "veto": {"version": cutoff_version, "thresholds": greeting_thresholds},
        "score": {
            "version": cutoff_version,
            "thresholds": greeting_thresholds | {"score_threshold_frames": veto.SCORE_THRESHOLD_FRAMES},
        },
    }


def _signal_detector_configs() -> dict:
    v1 = _bundled_detector_configs()["v1"]
    signal_version = {
        "_detector.py": _source_digest(Path(_detector.__file__)),
        "signals.py": _source_digest(Path(signals.__file__)),
    }
    phrases = files("whoanswered").joinpath(signals.CUE_RESOURCE).read_text(encoding="utf-8")
    cue_version = {signals.CUE_RESOURCE: sha256(phrases.encode("utf-8")).hexdigest()}
    transcript = {"final_only": True, "max_human_words": signals.MAX_HUMAN_WORDS}
    tone = {
        "min_hz": signals.TONE_MIN_HZ,
        "max_hz": signals.TONE_MAX_HZ,
        "step_hz": signals.TONE_STEP_HZ,
        "min_ms": signals.TONE_MIN_MS,
        "max_ms": signals.TONE_MAX_MS,
        "min_energy_share": signals.TONE_MIN_ENERGY_SHARE,
        "min_speech_ms": signals.SPEECH_MIN_MS,
        "active_mean_square": baseline.ACTIVE_MEAN_SQUARE,
    }
    return {
        "v1": v1,
        "transcript": {"version": signal_version | cue_version, "thresholds": transcript},
        "tone": {"version": signal_version | v1["version"], "thresholds": tone},
        "fusion": {
            "version": signal_version | cue_version | v1["version"],
            "thresholds": {
                "audio": v1["thresholds"], "transcript": transcript, "tone": tone,
                "human_agreement_cutoff_ms": CUTOFF_MS,
            },
        },
    }


def eligible_acoustic_cases(cases: Iterable[FixtureCase]) -> list[FixtureCase]:
    return [
        case for case in cases
        if case.track == "acoustic" and case.expected.matched_pair is None
        and case.expected.answerer in ANSWERERS
    ]


def acoustic_metrics(results: list[CaseReplay]) -> dict:
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
    detector_factories: Mapping[str, Callable[[], Detector]] | None = None,
    policy_pack: Path | None = None,
    policy_runner: PolicyRunner = run_policy,
) -> None:
    """Write one canonical JSON comparison report for the reserved split."""
    cases = [case for case in load_pack(pack) if case.split == "eval"]
    if not cases:
        raise FixtureValidationError("MISSING_EVAL_SPLIT", "manifest.json")
    eligible = eligible_acoustic_cases(cases)
    matched = [case for case in cases if case.expected.matched_pair is not None]
    policy_source = cases if policy_pack is None else load_pack(policy_pack)
    policy_cases = [case for case in policy_source if case.track == "policy"]
    if policy_pack is not None and not policy_cases:
        raise FixtureValidationError("MISSING_POLICY_CASES", "manifest.json")
    policy = policy_assertions(
        (case, replay_policy(case, policy_runner)) for case in policy_cases
    )

    is_v3 = cases[0].pack_version == SIGNAL_PACK_VERSION
    factories = detector_factories
    if factories is None:
        factories = _V3_FACTORIES if is_v3 else _V2_FACTORIES

    screeners = [
        case for case in cases if is_v3 and case.track == "acoustic" and case.expected.answerer == "screener"
    ]
    acoustic = {}
    prefixes = {}
    screener_verdicts = {}
    for name, factory in factories.items():
        acoustic[name] = acoustic_metrics([
            replay_case(case, factory(), cutoff_only=True) for case in eligible
        ])
        prefixes[name] = matched_prefix_assertions(
            replay_case(case, factory(), cutoff_only=True) for case in matched
        )
        if is_v3:
            screener_verdicts[name] = {
                case.case_id: replay_case(case, factory(), cutoff_only=True).initial_verdict for case in screeners
            }
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
        "frame_ms": FRAME_MS,
        "pack_manifest_sha256": sha256((pack / "manifest.json").read_bytes()).hexdigest(),
        "eligible_case_ids": [case.case_id for case in eligible],
        "acoustic": acoustic,
        "matched_prefix_assertions": prefixes,
        "policy_assertions": policy,
        "promotion": promotion,
    }
    if detector_factories is None:
        report["detectors"] = _signal_detector_configs() if is_v3 else _bundled_detector_configs()
    if is_v3:
        report["screener_verdicts"] = screener_verdicts
        report["signal_provenance"] = {
            "transcripts_and_audio": "authored synthetic",
            "real_asr_output_tested": False,
            "real_phone_audio_tested": False,
        }
    if policy_pack is not None:
        policy_manifest = (policy_pack / "manifest.json").read_bytes()
        report["policy_pack_manifest_sha256"] = sha256(policy_manifest).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_bytes(canonical_json(report))
