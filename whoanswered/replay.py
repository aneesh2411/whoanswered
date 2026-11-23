"""Replay a validated local fixture pack."""

import hashlib
from pathlib import Path

from .fixtures import ObservableInput, canonical_json, load_pack


def replay(pack: Path, out: Path) -> None:
    cases = load_pack(pack)
    for case in cases:
        case_out = out if len(cases) == 1 else out / case.case_id
        _replay_case(case.observable, case_out)


def _replay_case(observable: ObservableInput, out: Path) -> None:
    result = {"decision": "uncertain", "provisional": True}
    trace = [
        {"event": "fixture_read", "sha256": hashlib.sha256(observable.audio).hexdigest()},
        {"event": "decision", "reason": "classification_not_implemented"},
    ]

    out.mkdir(parents=True, exist_ok=True)
    for filename, payload in (("result.json", result), ("trace.json", trace)):
        (out / filename).write_bytes(canonical_json(payload))
