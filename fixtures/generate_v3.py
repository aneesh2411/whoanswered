"""Generate v3 with authored synthetic transcripts and audio using integer synthesis."""

import argparse
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from fixtures.generate_v1 import (
    SAMPLE_RATE, SAMPLES_PER_MS, SINE, Case, Source, audio_for, events_for, mulaw, utterance,
)
from whoanswered.fixtures import SCHEMA_VERSION, canonical_json, canonical_jsonl


@dataclass(frozen=True)
class Scenario:
    id: str
    family: str
    answerer: str
    speech: tuple[tuple[int, str], ...]
    transcript: tuple[tuple[int, str, bool], ...]
    tone: tuple[int, int, int] | None = None  # Start and duration in ms, frequency in Hz.
    pair: str | None = None
    verdict: str = "uncertain"


def scenarios(split: str) -> list[Scenario]:
    dev = split == "dev"
    greeting = "hello?" if dev else "hi?"
    open_question = "Hello, who is calling?" if dev else "Hi, is this Morgan?"
    name = "Hi, this is Alex." if dev else "Hey, this is Sam."
    mailbox = "Please leave a message." if dev else "You reached the voice mailbox."
    alternate = "I'm not available." if dev else "Start speaking at the beep."
    screener = "Please record your name." if dev else "This call is being screened."
    return [
        Scenario("human-question", "agreement", "human", ((0, greeting),),
                 ((340, "Hello", False), (510, open_question, True)), verdict="human"),
        Scenario("human-name", "agreement", "human", ((0, "hi"),),
                 ((440, name, True),), verdict="human"),
        Scenario("human-at-cutoff", "agreement", "human", ((0, "hey"),),
                 ((2000, name, True),), verdict="human"),
        Scenario("human-corrected-partial", "transcript-noise", "human", ((0, "yes?"),),
                 ((300, mailbox, False), (700, open_question, True)), verdict="human"),
        Scenario("human-bad-final", "transcript-noise", "human", ((0, greeting),),
                 ((570, alternate, True),)),
        Scenario("human-empty", "transcript-noise", "human", ((0, "hi"),),
                 ((500, "", True),)),
        Scenario("human-long", "disagreement", "human",
                 ((0, "hello yes i'm here go ahead who is calling?"),), ((1540, open_question, True),)),
        Scenario("human-brief-tone", "tone-control", "human", ((0, "hey"),),
                 ((520, open_question, True),), (1040, 100, 730 if dev else 1020), verdict="human"),
        Scenario("mailbox-wording", "agreement", "voicemail",
                 ((0, "i'm away please leave a note"),), ((1610, mailbox, True),), verdict="voicemail"),
        Scenario("mailbox-beep", "beep", "voicemail", ((0, "leave a note"),),
                 ((800, alternate, True),), (1140, 200, 875 if dev else 1337), verdict="voicemail"),
        Scenario("mailbox-beep-only", "beep", "voicemail", ((0, "can't answer"),),
                 ((900, "… [inaudible]", True),), (1300, 180, 427 if dev else 1977), verdict="voicemail"),
        Scenario("mailbox-wrong-human", "disagreement", "voicemail", ((0, "hi"),),
                 ((550, open_question, True),), (1340, 240, 1030 if dev else 1723), verdict="voicemail"),
        Scenario("mailbox-late-tone", "late-signal", "voicemail", ((0, "leave a note"),),
                 ((960, "Please call back later.", True),), (2040, 220, 940 if dev else 1588)),
        Scenario("mailbox-late-final", "late-signal", "voicemail",
                 ((0, "i'm away please leave a note"),), ((1300, mailbox, False), (2001, mailbox, True))),
        Scenario("mailbox-short-no-tone", "disagreement", "voicemail", ((0, "leave a"),),
                 ((530, mailbox, True),)),
        Scenario("screener", "screener", "screener", ((0, "please say your name"),),
                 ((850, screener, True),), verdict="screener"),
        *matched_pair("paused", ((0, greeting), (680, "yes?")), ((950, "Hello, yes?", False),)),
        *matched_pair("ambiguous", ((100, "go ahead"),), ((720, "Go ahead.", True),)),
    ]


def matched_pair(name, speech, transcript):
    return [
        Scenario(f"{name}-human", "matched-prefix", "human",
                 speech + ((2200, "yes i'm here"),), transcript + ((2920, "Yes, I'm here.", True),),
                 pair=name),
        Scenario(f"{name}-mailbox", "matched-prefix", "voicemail",
                 speech + ((2200, "please leave a note"),),
                 transcript + ((3160, "Please leave a message.", True),),
                 (3300, 180, 1120), pair=name),
    ]


def audio_with_tone(case: Case, tone: tuple[int, int, int] | None) -> bytes:
    audio = audio_for(case)
    if tone is None:
        return audio
    at_ms, duration, hz = tone
    start = at_ms * SAMPLES_PER_MS
    stop = start + duration * SAMPLES_PER_MS
    if stop > len(audio) or any(
        t * SAMPLES_PER_MS < stop and t * SAMPLES_PER_MS
        + len(utterance(phrase, case.source.seed + i, case.source.pitch, case.source.tempo)) > start
        for i, (t, phrase) in enumerate(case.clips)
    ):
        raise ValueError(f"{case.id} has an overlapping or out-of-bounds tone")
    beep = bytes(mulaw(5 * SINE[i * hz * 1024 // SAMPLE_RATE % 1024]) for i in range(stop - start))
    return audio[:start] + beep + audio[stop:]


def events_with_transcript(case: Case, transcript: tuple[tuple[int, str, bool], ...]) -> bytes:
    events = [json.loads(line) for line in events_for(case).splitlines()]
    events.extend(
        {"event": "transcript", "arrival_ms": at_ms, "text": text, "final": final}
        for at_ms, text, final in transcript
    )
    events.sort(key=lambda event: event["arrival_ms"])
    for sequence, event in enumerate(events, 1):
        event["sequence"] = sequence
    return canonical_jsonl(events)


def generate(out: Path) -> None:
    for split_index, split in enumerate(("dev", "eval")):
        pack = out / split
        entries = []
        seeds = {}
        for scenario in sorted(scenarios(split), key=lambda item: item.id):
            group = f"{split}-{scenario.pair or scenario.id}"
            seed = seeds.setdefault(group, 34000 + 100 * split_index + len(seeds))
            case = Case(
                id=f"{split}-{scenario.id}", family=scenario.family,
                source=Source(group, seed, 124 + 17 * split_index + seed % 17, 91 + 4 * split_index),
                description="Authored synthetic audio and transcript with scripted arrival times.",
                answerer=scenario.answerer, duration_ms=3600 if scenario.pair else 2640,
                clips=scenario.speech, room_tone=split == "eval",
            )
            expected = {
                "answerer": case.answerer,
                "matched_pair": group if scenario.pair else None,
                "verdict": scenario.verdict,
                "actions": [],
            }
            (pack / case.id).mkdir(parents=True, exist_ok=True)
            payloads = {}
            for filename, data in (
                ("audio.ulaw", audio_with_tone(case, scenario.tone)),
                ("events.jsonl", events_with_transcript(case, scenario.transcript)),
                ("expected.json", canonical_json(expected)),
            ):
                path = pack / case.id / filename
                path.write_bytes(data)
                payloads[filename] = {"path": path.relative_to(pack).as_posix(), "sha256": sha256(data).hexdigest()}
            entries.append({
                "id": case.id, "family": case.family, "license": "MIT", "payloads": payloads,
                "provenance": case.description, "source_group": group, "seed": seed,
                "split": split, "track": "acoustic",
            })
        (pack / "manifest.json").write_bytes(canonical_json({
            "schema_version": SCHEMA_VERSION, "pack_version": 3, "cases": entries,
        }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("v3"))
    generate(parser.parse_args().out)
