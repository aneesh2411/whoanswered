"""Generate the revision 2 development and reserved evaluation packs."""

import argparse
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from fixtures.generate_v1 import Case, Source, audio_for, events_for
from whoanswered.fixtures import SCHEMA_VERSION, canonical_json


@dataclass(frozen=True)
class Opening:
    seed: int
    pitch: int
    tempo: int
    at_ms: int
    phrase: str


# New source recipes for each split. Only the integer synthesizer and
# phonetic vocabulary are shared with v1.
OPENINGS = {
    "dev": {
        "short-human": Opening(21901, 160, 91, 60, "hello?"),
        "short-mailbox": Opening(21902, 147, 92, 100, "leave a note"),
        "long-human": Opening(21903, 128, 114, 100, "hello yes i'm here go ahead who is calling?"),
        "resumed-human": Opening(21904, 125, 97, 40, "yes?"),
        "paused-mailbox": Opening(21905, 172, 92, 80, "hi"),
        "matched-prefix": Opening(21906, 142, 105, 160, "hello?"),
    },
    "eval": {
        "short-human": Opening(21911, 115, 105, 180, "hey?"),
        "short-mailbox": Opening(21912, 133, 91, 60, "can't answer"),
        "long-human": Opening(21913, 165, 102, 180, "hi yes i'm here go ahead who is calling?"),
        "resumed-human": Opening(21914, 154, 95, 140, "hi?"),
        "paused-mailbox": Opening(21915, 113, 109, 160, "hey"),
        "matched-prefix": Opening(21916, 121, 110, 260, "hey?"),
    },
}
RESUME_TIMES = {"dev": (980, 1980, 2240), "eval": (1140, 1980, 2320)}
RESUME_PHRASES = {
    "dev": {"resumed-human": "yes i'm here", "paused-mailbox": "i'm away leave a note"},
    "eval": {"resumed-human": "go ahead who is calling?", "paused-mailbox": "please call back later"},
}


def opening_case(split: str, family: str, answerer: str, duration_ms: int) -> Case:
    opening = OPENINGS[split][family]
    group = f"{split}-{family}"
    return Case(
        group, family, Source(group, opening.seed, opening.pitch, opening.tempo),
        f"Synthetic opening '{opening.phrase}' at {opening.at_ms} ms.", answerer, duration_ms,
        clips=((opening.at_ms, opening.phrase),),
    )


def variant(case: Case, suffix: str, description: str, **changes) -> Case:
    return replace(case, id=f"{case.id}-{suffix}",
                   description=f"{case.description} {description}", **changes)


def continuing_cases(case: Case, split: str) -> Iterator[Case]:
    phrase = RESUME_PHRASES[split][case.family]
    for timing, resume in zip(("before", "near", "after"), RESUME_TIMES[split], strict=True):
        description = f"After a pause, '{phrase}' starts at {resume} ms."
        tail = case.clips + ((resume, phrase),)
        beep = resume + 1640 if case.answerer == "voicemail" else None
        base = variant(case, timing, description, clips=tail, beep_at_ms=beep)
        yield base
        if timing == "near":
            yield variant(base, "room-tone", "Adds low-level room tone between speech bursts.",
                          room_tone=True)
        else:
            # Leave the opening intact and omit delivered frames in the pause.
            yield variant(base, "media-gap", "Drops media covering 600 to 800 ms of audio.",
                          media_windows=((0, 600), (800, case.duration_ms)))


def matched_cases(split: str) -> Iterator[Case]:
    if split == "dev":
        second, resume = (640, "yes?"), 2180
        human, mailbox = "i'm here go ahead", "i'm away leave a note"
    else:
        second, resume = (660, "hello?"), 2320
        human, mailbox = "yes go ahead", "can't answer leave your note"
    base = opening_case(split, "matched-prefix", "human", 4400)
    prefix = base.clips + (second,)
    for ending, answerer, phrase, beep in (
        ("human", "human", human, None),
        ("mailbox", "voicemail", mailbox, resume + 1760),
    ):
        yield variant(
            base, ending,
            f"Both calls add '{second[1]}' at {second[0]} ms and pause through cutoff. "
            f"The {ending} continuation '{phrase}' starts at {resume} ms.",
            answerer=answerer, clips=prefix + ((resume, phrase),), beep_at_ms=beep,
            matched_pair=base.source.group, room_tone=split == "eval",
        )


def cases(split: str) -> Iterator[Case]:
    pickup = opening_case(split, "short-human", "human", 2600)
    pickup = replace(pickup, verdict="human")
    yield variant(pickup, "clean", "The human waits after the short pickup.")
    yield variant(pickup, "room-tone", "The human waits with low-level room tone.", room_tone=True)

    mailbox = opening_case(split, "short-mailbox", "voicemail", 2600)
    yield variant(mailbox, "tone", "A recording tone starts at 1080 ms.", beep_at_ms=1080)
    yield variant(mailbox, "room-tone", "The short greeting ends in low-level room tone.",
                  room_tone=True)

    greeting = opening_case(split, "long-human", "human", 3400)
    yield variant(greeting, "clean", "The human continues speaking across cutoff.")
    yield variant(greeting, "media-gap", "Drops media covering 760 to 920 ms of the greeting.",
                  media_windows=((0, 760), (920, greeting.duration_ms)))

    yield from continuing_cases(opening_case(split, "resumed-human", "human", 4400), split)
    yield from continuing_cases(opening_case(split, "paused-mailbox", "voicemail", 4400), split)
    yield from matched_cases(split)


def generate(out: Path) -> None:
    for split in OPENINGS:
        pack = out / split
        entries = []
        for case in sorted(cases(split), key=lambda case: case.id):
            (pack / case.id).mkdir(parents=True, exist_ok=True)
            expected = {
                "answerer": case.answerer,
                "matched_pair": case.matched_pair,
                "verdict": case.verdict,
                "actions": [],
            }
            payloads = {}
            for filename, data in (
                ("audio.ulaw", audio_for(case)),
                ("events.jsonl", events_for(case)),
                ("expected.json", canonical_json(expected)),
            ):
                path = pack / case.id / filename
                path.write_bytes(data)
                payloads[filename] = {
                    "path": path.relative_to(pack).as_posix(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            entries.append({
                "id": case.id,
                "family": case.family,
                "license": "MIT",
                "payloads": payloads,
                "provenance": f"{case.description} First-party synthetic audio and scripted events.",
                "source_group": case.source.group,
                "seed": case.source.seed,
                "split": split,
                "track": "acoustic",
            })
        (pack / "manifest.json").write_bytes(canonical_json({
            "schema_version": SCHEMA_VERSION,
            "pack_version": 2,
            "cases": entries,
        }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("v2"))
    generate(parser.parse_args().out)
