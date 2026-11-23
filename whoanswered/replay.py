"""Replay the local smoke fixture."""

import hashlib
import json
from pathlib import Path


def replay(pack: Path, out: Path) -> None:
    fixture = (pack / "greeting.txt").read_bytes()
    result = {"decision": "uncertain", "provisional": True}
    trace = [
        {"event": "fixture_read", "sha256": hashlib.sha256(fixture).hexdigest()},
        {"event": "decision", "reason": "classification_not_implemented"},
    ]

    out.mkdir(parents=True, exist_ok=True)
    for filename, payload in (("result.json", result), ("trace.json", trace)):
        output = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        (out / filename).write_bytes(output.encode("utf-8"))
