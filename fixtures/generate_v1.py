"""Generate the public v1 fixtures using only integer synthesis and scripted events."""

import argparse
import hashlib
import random
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from whoanswered.fixtures import (
    FRAME_BYTES,
    FRAME_MS,
    PACK_VERSION,
    SCHEMA_VERSION,
    canonical_json,
    canonical_jsonl,
)


SAMPLES_PER_MS = FRAME_BYTES // FRAME_MS
SAMPLE_RATE = 1_000 * SAMPLES_PER_MS
SILENCE = b"\xff"
STREAM_OPEN = (("connected", 0), ("start", 0))
MATCHED_PAIR = "hello-2000"

# Voiced phones use three formants in Hz. The unvoiced phones below use
# filtered noise. Word transcriptions and durations are authored here.
FORMANTS = {
    "ae": (660, 1720, 2410), "ah": (720, 1180, 2540), "aw": (570, 860, 2410),
    "ee": (280, 2200, 3000), "eh": (530, 1770, 2490), "er": (480, 1370, 1700),
    "ih": (390, 1990, 2550), "oh": (460, 820, 2460), "oo": (300, 760, 2240),
    "uh": (500, 1480, 2480), "l": (350, 1200, 2590), "r": (340, 1360, 1680),
    "m": (260, 1050, 2150), "n": (290, 1450, 2600), "ng": (300, 1800, 2490),
    "w": (300, 850, 2240), "y": (290, 2130, 2900),
    "b": (300, 1050, 2300), "d": (300, 1700, 2550), "g": (330, 1600, 2200),
    "v": (320, 1400, 2420), "z": (320, 1850, 2900),
}
WORDS = {
    "a": "uh:55",
    "ahead": "uh:65 h:38 eh:115 d:36",
    "answer": "ae:112 n:65 s:68 er:115",
    "away": "uh:70 w:45 eh:130 ee:65",
    "back": "b:34 ae:120 k:45",
    "call": "k:38 aw:132 l:68",
    "calling": "k:36 aw:112 l:58 ih:80 ng:65",
    "can't": "k:36 ae:100 n:55 t:36",
    "go": "g:33 oh:115 oo:48",
    "hello": "h:42 uh:63 l:47 oh:128 oo:54",
    "here": "h:38 ee:122 r:58",
    "hey": "h:45 eh:125 ee:70",
    "hi": "h:48 ah:115 ee:80",
    "i'm": "ah:92 ee:52 m:46",
    "is": "ih:75 z:52",
    "later": "l:40 eh:110 ee:48 t:34 er:98",
    "leave": "l:44 ee:120 v:58",
    "name": "n:46 eh:94 ee:64 m:68",
    "note": "n:46 oh:128 oo:50 t:40",
    "please": "p:36 l:47 ee:125 z:64",
    "say": "s:62 eh:112 ee:57",
    "who": "h:50 oo:125",
    "yes": "y:43 eh:138 s:87",
    "your": "y:38 aw:81 r:56",
}


def sine(phase: int) -> int:
    """Integer approximation to sine, scaled to +/-1024 over 1024 steps."""
    half, step = divmod(phase, 512)
    magnitude = step * (512 - step) // 64
    return -magnitude if half else magnitude


SINE = tuple(sine(phase) for phase in range(1024))


def mulaw(sample: int) -> int:
    """Encode one signed 16-bit PCM sample as G.711 mu-law."""
    sign = 0x80 if sample < 0 else 0
    biased = min(abs(sample), 32635) + 132
    exponent = biased.bit_length() - 8
    mantissa = (biased >> (exponent + 3)) & 15
    return (sign | (exponent << 4) | mantissa) ^ 0xFF


def harmonics(phone: str, pitch: int) -> list[tuple[int, int]]:
    formants = tuple(zip(FORMANTS[phone], (170, 230, 300), (72, 44, 26), strict=True))
    weights = []
    for harmonic in range(1, 3400 // pitch + 1):
        hz = harmonic * pitch
        weight = sum(max(0, width - abs(hz - center)) * gain // width
                     for center, width, gain in formants)
        weight = weight * 12 // (12 + harmonic)
        if harmonic == 1:
            weight += 12
        if weight:
            weights.append((harmonic, weight))
    return weights


@cache
def utterance(text: str, seed: int, pitch: int, tempo: int) -> bytes:
    """Render a phonetic script with shaped harmonics, breath and pitch movement."""
    rng = random.Random(seed)
    speech = bytearray()
    for word in text.rstrip("?").split():
        if speech:
            speech.extend(SILENCE * (18 * SAMPLES_PER_MS))
        phones = WORDS[word].split()
        phase = 0
        for index, token in enumerate(phones):
            phone, duration = token.split(":")
            length = int(duration) * tempo * SAMPLES_PER_MS // 100
            base_pitch = pitch + rng.randrange(-6, 7)
            # Questions lift on the final vowel. Declarative words taper down.
            glide = 32 if text.endswith("?") and index >= len(phones) - 2 else -9
            weights = harmonics(phone, base_pitch) if phone in FORMANTS else []
            total_weight = sum(weight for _, weight in weights)
            previous_noise = 0
            for i in range(length):
                phase += (base_pitch + glide * i // length) * 1024
                noise = rng.randrange(-256, 256)
                breath = noise - previous_noise
                previous_noise = noise
                if weights:
                    voiced = sum(SINE[(phase * h // SAMPLE_RATE) % 1024] * weight
                                 for h, weight in weights)
                    sample = voiced * 9 // total_weight
                    if phone in {"v", "z"}:
                        sample += breath * 9
                else:
                    sample = breath * (6 if phone == "h" else 14)
                    if phone in {"k", "p", "t"} and i < length // 2:
                        sample = 0
                envelope = min(i, length - 1 - i, 32)
                speech.append(mulaw(sample * envelope // 32))
    return bytes(speech)


@dataclass(frozen=True)
class Source:
    group: str
    seed: int
    pitch: int = 135
    tempo: int = 100


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    source: Source
    description: str
    answerer: str
    duration_ms: int
    verdict: str = "uncertain"
    clips: tuple[tuple[int, str], ...] = ()
    beep_at_ms: int | None = None
    room_tone: bool = False
    media_windows: tuple[tuple[int, int], ...] | None = None
    media_delay_ms: int = 0
    signals: tuple[tuple[str, int], ...] = STREAM_OPEN
    matched_pair: str | None = None
    policy_events: tuple[tuple[str, int], ...] = ()
    actions: tuple[tuple[str, int], ...] = ()


def cases() -> Iterator[Case]:
    hello = Source("pickup-hello", 10601, 156, 94)
    yes = Source("pickup-yes", 10602, 120)
    for suffix, source, greeting, start in (
        ("hello", hello, "hello?", 0),
        ("hello-delayed", hello, "hello?", 420),
        ("yes", yes, "yes?", 80),
        ("yes-late", yes, "yes?", 940),
    ):
        yield Case(f"clean-pickup-{suffix}", "clean-pickup", source,
                   f"Human pickup, '{greeting}' at {start} ms, then waits.",
                   "human", 2400, "human", ((start, greeting),))

    note = Source("mailbox-note", 10603, 151, 95)
    unavailable = Source("mailbox-unavailable", 10604, 112)
    for suffix, source, greeting, start, beep in (
        ("note", note, "leave a note", 40, 1040),
        ("note-no-beep", note, "leave a note", 300, None),
        ("unavailable", unavailable, "can't answer", 0, 960),
        ("unavailable-no-beep", unavailable, "can't answer", 620, None),
    ):
        ending = "a recording tone" if beep is not None else "silence"
        yield Case(f"short-mailbox-{suffix}", "short-mailbox", source,
                   f"Brief mailbox greeting, '{greeting}' at {start} ms, then {ending}.",
                   "voicemail", 2400, clips=((start, greeting),), beep_at_ms=beep)

    away = Source("mailbox-away", 10605, 143)
    later = Source("mailbox-later", 10606, 116, 104)
    for suffix, source, greeting, continuation, resume, beep in (
        ("away", away, "hi", "i'm away", 780, 2540),
        ("away-long-gap", away, "hi", "i'm away", 1820, 3580),
        ("later", later, "hey", "call back later", 620, 1720),
        ("later-long-gap", later, "hey", "call back later", 2200, 3300),
    ):
        clips = ((0, greeting), (resume, continuation))
        if source == away:
            clips += ((resume + 780, "leave a note"),)
        yield Case(f"paused-mailbox-{suffix}", "paused-mailbox", source,
                   f"Mailbox opens with '{greeting}', pauses, and resumes at {resume} ms.",
                   "voicemail", beep + 360, clips=clips, beep_at_ms=beep)

    silence = Source("connected-silence", 10607)
    yield Case("connected-silence-digital", "connected-silence", silence,
               "The connected stream delivers digital silence.", "silence", 2400)
    yield Case("connected-silence-delayed", "connected-silence", silence,
               "The connected stream delivers silent frames with 240 ms of latency.",
               "silence", 2400, media_delay_ms=240)
    yield Case("connected-silence-room-tone", "connected-silence", silence,
               "The connected stream delivers low-level synthetic room tone.",
               "silence", 2400, room_tone=True)

    loss = Source("media-loss", 10608)
    yield Case("media-loss-no-frames", "media-loss", loss,
               "The stream starts but delivers no media.", "unknown", 0)
    yield Case("media-loss-stream-stop", "media-loss", loss,
               "The media stream stops after 240 ms of silence.", "unknown", 240,
               signals=STREAM_OPEN + (("stop", 260),))
    yield Case("media-loss-gap-and-resume", "media-loss", loss,
               "A midstream gap drops frames before media resumes.", "unknown", 2460,
               clips=((680, "hello?"),), media_windows=((0, 200), (1520, 2460)))

    disconnect = Source("confirmed-disconnect", 10609)
    yield Case("confirmed-disconnect-before-stream", "confirmed-disconnect", disconnect,
               "Call completion is confirmed before the media stream opens.",
               "disconnect", 0, signals=(("call_completed", 380),))
    for suffix, stop, complete in (("at-cutoff", 1400, 2000), ("late", 680, 2080)):
        yield Case(f"confirmed-disconnect-{suffix}", "confirmed-disconnect", disconnect,
                   f"Stream stops at {stop} ms. Call completion is confirmed at {complete} ms.",
                   "disconnect", stop - 20,
                   signals=STREAM_OPEN + (("stop", stop), ("call_completed", complete)))

    # Each set of outcome variants reuses the same prompt audio and policy signals.
    for name, source, prompt, prompt_end, transition in (
        ("name", Source("simulated-screener-name", 10610, 168), "please say your name", 1180, 1980),
        ("caller", Source("simulated-screener-caller", 10611, 123), "who is calling", 1020, 2420),
    ):
        for ending, answerer, event, action, tail in (
            ("human", "human", "human_takeover", "start_conversation", "go ahead"),
            ("mailbox", "voicemail", "mailbox_ready", "play_voicemail", ""),
            ("disconnect", "disconnect", "disconnect", "cancel_playback", ""),
        ):
            clips = ((80, prompt),)
            signals = STREAM_OPEN
            duration = transition + 720
            beep = None
            if tail:
                clips += ((transition, tail),)
            if ending == "mailbox":
                # The mailbox finishes its brief greeting before it becomes ready.
                clips += ((transition - 740, "leave a note"),)
                beep = transition - 120
            if ending == "disconnect":
                duration = transition - FRAME_MS
                signals += (("stop", transition), ("call_completed", transition))
            yield Case(
                f"simulated-screener-{name}-to-{ending}", f"screener-to-{ending}", source,
                f"Simulated generic screener, '{prompt}', followed by {ending}.",
                answerer, duration, clips=clips, beep_at_ms=beep, signals=signals,
                policy_events=(("screener", 80), ("prompt_end", prompt_end), (event, transition)),
                actions=(("reply_identity_and_purpose", prompt_end), (action, transition)),
            )

    pair = Source("matched-hello", 10612, 154, 132)
    for ending, answerer, tail, beep, duration in (
        ("human", "human", "yes i'm here", None, 3240),
        ("mailbox", "voicemail", "leave your note", 3220, 3540),
    ):
        yield Case(f"matched-hello-{ending}", "matched-hello", pair,
                   f"Shared synthetic 'Hello?' and pause, then a {ending} continuation.",
                   answerer, duration, clips=((180, "hello?"), (2140, tail)),
                   beep_at_ms=beep, matched_pair=MATCHED_PAIR)


def audio_for(case: Case) -> bytes:
    size = case.duration_ms * SAMPLES_PER_MS
    audio = bytearray(SILENCE * size)
    if case.room_tone:
        rng = random.Random(case.source.seed)
        audio = bytearray(mulaw(rng.randrange(-48, 49)) for _ in range(size))
    previous_end = 0
    for index, (at_ms, phrase) in enumerate(case.clips):
        clip = utterance(phrase, case.source.seed + index, case.source.pitch, case.source.tempo)
        start = at_ms * SAMPLES_PER_MS
        end = start + len(clip)
        if not previous_end <= start <= end <= size:
            raise ValueError(f"{case.id} has an overlapping or out-of-bounds utterance")
        audio[start:end] = clip
        previous_end = end
    if case.beep_at_ms is not None:
        start = case.beep_at_ms * SAMPLES_PER_MS
        end = start + 120 * SAMPLES_PER_MS
        if not previous_end <= start <= end <= size:
            raise ValueError(f"{case.id} has an overlapping or out-of-bounds tone")
        audio[start:end] = bytes(
            mulaw(6 * SINE[(i * 960 * 1024 // SAMPLE_RATE) % 1024])
            for i in range(end - start)
        )
    return bytes(audio)


def events_for(case: Case) -> bytes:
    stream = [(kind, at_ms, None) for kind, at_ms in (("call_answered", 0),) + case.signals]
    windows = case.media_windows if case.media_windows is not None else ((0, case.duration_ms),)
    for start, end in windows:
        for at_ms in range(start, end, FRAME_MS):
            stream.append(("media", at_ms + FRAME_MS + case.media_delay_ms, at_ms // FRAME_MS))
    records = []
    for sequence, (kind, at_ms, frame) in enumerate(sorted(stream, key=lambda e: e[1]), 1):
        record = {"event": kind, "arrival_ms": at_ms, "sequence": sequence}
        if frame is not None:
            record["media"] = {"frame_index": frame}
        records.append(record)
    return canonical_jsonl(records)


def generate(out: Path) -> None:
    entries = []
    for case in sorted(cases(), key=lambda case: case.id):
        case_dir = out / case.id
        case_dir.mkdir(parents=True, exist_ok=True)
        expected = {
            "answerer": case.answerer,
            "matched_pair": case.matched_pair,
            "verdict": case.verdict,
            "actions": [{"action": action, "at_ms": at_ms} for action, at_ms in case.actions],
        }
        payloads = {}
        for filename, data in (
            ("audio.ulaw", audio_for(case)),
            ("events.jsonl", events_for(case)),
            ("expected.json", canonical_json(expected)),
        ):
            path = case_dir / filename
            path.write_bytes(data)
            payloads[filename] = {
                "path": path.relative_to(out).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        entry = {
            "id": case.id,
            "family": case.family,
            "license": "MIT",
            "payloads": payloads,
            "provenance": f"{case.description} First-party synthetic audio and scripted events.",
            "source_group": case.source.group,
            "seed": case.source.seed,
            "track": "policy" if case.policy_events else "acoustic",
        }
        if case.policy_events:
            entry["simulated"] = True
            entry["policy_events"] = [
                {"event": event, "at_ms": at_ms} for event, at_ms in case.policy_events
            ]
        entries.append(entry)
    (out / "manifest.json").write_bytes(canonical_json({
        "schema_version": SCHEMA_VERSION,
        "pack_version": PACK_VERSION,
        "cases": entries,
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("v1"))
    generate(parser.parse_args().out)
