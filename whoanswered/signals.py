"""Rule-based detection from authored synthetic transcript and audio signals."""

import json
import re
from importlib.resources import files
from math import cos, pi

from ._detector import Decision, Detector, DetectorSession, V1Detector, _Label
from .baseline import ACTIVE_MEAN_SQUARE, decode_mulaw, is_active_frame
from .fixtures import FRAME_BYTES, FRAME_MS


CUE_RESOURCE = "transcript_cues.json"
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_CUES = json.loads(files("whoanswered").joinpath(CUE_RESOURCE).read_text(encoding="utf-8"))
MAX_HUMAN_WORDS = 8
TONE_MIN_HZ = 400
TONE_MAX_HZ = 2000
TONE_STEP_HZ = 25
TONE_MIN_MS = 160
TONE_MAX_MS = 600
TONE_MIN_ENERGY_SHARE = 0.80
SPEECH_MIN_MS = 40
_SAMPLE_RATE = 1000 * FRAME_BYTES // FRAME_MS
_PCM = tuple(decode_mulaw(code) for code in range(256))
_BANK = tuple(
    (frequency, 2 * cos(2 * pi * frequency / _SAMPLE_RATE))
    for frequency in range(TONE_MIN_HZ - TONE_STEP_HZ, TONE_MAX_HZ + 2 * TONE_STEP_HZ, TONE_STEP_HZ)
)


def _transcript_cue(text: str) -> tuple[_Label, str] | None:
    original = _WORD.findall(text)
    words = [word.casefold().replace("’", "'") for word in original]
    phrase = f" {' '.join(words)} "
    mailbox = any(f" {cue} " in phrase for cue in _CUES["voicemail"])
    screener = any(f" {cue} " in phrase for cue in _CUES["screener"])
    if mailbox and screener:
        return None
    if mailbox:
        return "voicemail", "voicemail_phrase"
    if screener:
        return "screener", "screener_phrase"
    if not 2 <= len(words) <= MAX_HUMAN_WORDS or words[0] not in _CUES["greetings"]:
        return None

    trailing_words = words[1:]
    tail = " ".join(trailing_words) + " "
    if text.rstrip().endswith("?") or any(
        tail.startswith(f"{cue} ") for cue in _CUES["human_question_openers"]
    ):
        return "human", "greeting_with_question"
    if any(
        tail.startswith(f"{cue} ") and len(trailing_words) > len(cue.split())
        and trailing_words[len(cue.split())] not in _CUES["human_name_exclusions"]
        for cue in _CUES["human_name_openers"]
    ) or (
        len(words) == 2 and original[1].istitle() and words[1] not in _CUES["human_name_exclusions"]
    ):
        return "human", "greeting_with_name"
    return None


class _SignalSession(DetectorSession):
    def __init__(self) -> None:
        self._decision = Decision("uncertain", 0, False, "insufficient_evidence")
        self._closed = False

    def decision(self) -> Decision:
        return self._decision

    def feed_audio(
        self, frame: bytes, *, t_ms: int, encoding: str = "mulaw8k",
    ) -> Decision | None:
        return None

    def feed_event(self, name: str, *, t_ms: int) -> Decision | None:
        if name == "call_completed":
            self._closed = True
        return None


class TranscriptCueDetector:
    """Commit on the first finalized transcript with an unambiguous phrase cue."""

    def start(self) -> DetectorSession:
        return _TranscriptSession()


class _TranscriptSession(_SignalSession):
    def feed_transcript(self, text: str, *, t_ms: int, final: bool) -> Decision | None:
        if final and not self._closed and not self._decision.committed:
            cue = _transcript_cue(text)
            if cue is not None:
                label, reason = cue
                self._decision = Decision(label, t_ms, True, reason)
                return self._decision
        return None


def _tone_frequency(frame: bytes) -> int | None:
    decoded = [_PCM[code] for code in frame]
    mean = sum(decoded) / FRAME_BYTES
    samples = [sample - mean for sample in decoded]
    energy = sum(sample * sample for sample in samples)
    if energy < ACTIVE_MEAN_SQUARE * FRAME_BYTES:
        return None

    peak = 0.0
    best = None
    for frequency, coefficient in _BANK:
        previous = before_previous = 0.0
        for sample in samples:
            previous, before_previous = sample + coefficient * previous - before_previous, previous
        power = previous**2 + before_previous**2 - coefficient * previous * before_previous
        if power > peak:
            peak, best = power, frequency
    if (best is not None and TONE_MIN_HZ <= best <= TONE_MAX_HZ
            and 2 * peak >= TONE_MIN_ENERGY_SHARE * FRAME_BYTES * energy):
        return best
    return None


def _check_audio(frame: bytes, t_ms: int, encoding: str) -> None:
    if encoding != "mulaw8k" or len(frame) != FRAME_BYTES:
        raise ValueError(f"expected {FRAME_BYTES}-byte mulaw8k audio frames")
    if t_ms < FRAME_MS or t_ms % FRAME_MS:
        raise ValueError(f"audio t_ms must be a positive multiple of {FRAME_MS}")


class ToneDetector:
    """Recognize a bounded, steady tone after speech in 8 kHz G.711 mu-law."""

    def start(self) -> DetectorSession:
        return _ToneSession()


class _ToneSession(_SignalSession):
    def __init__(self) -> None:
        super().__init__()
        self._last_sample_ms: int | None = None
        self._stream_lost = False
        self._speech_ms = 0
        self._heard_speech = False
        self._tone_hz: int | None = None
        self._tone_ms = 0

    def feed_event(self, name: str, *, t_ms: int) -> Decision | None:
        if name == "stop":
            self._stream_lost = True
            self._tone_hz = self._last_sample_ms = None
            self._speech_ms = self._tone_ms = 0
        elif name == "start":
            self._stream_lost = False
        return super().feed_event(name, t_ms=t_ms)

    def feed_audio(
        self, frame: bytes, *, t_ms: int, encoding: str = "mulaw8k",
    ) -> Decision | None:
        _check_audio(frame, t_ms, encoding)
        return self._feed_audio_at(frame, t_ms=t_ms, sample_start_ms=t_ms - FRAME_MS)

    def _feed_audio_at(self, frame: bytes, *, t_ms: int, sample_start_ms: int) -> Decision | None:
        if self._closed or self._decision.committed or self._stream_lost:
            return None
        if self._last_sample_ms is not None:
            if sample_start_ms <= self._last_sample_ms:
                return None
            if sample_start_ms != self._last_sample_ms + FRAME_MS:
                self._tone_hz = None
                self._speech_ms = self._tone_ms = 0
        self._last_sample_ms = sample_start_ms

        hz = _tone_frequency(frame)
        if hz is not None:
            self._speech_ms = 0
            if self._tone_hz is None or abs(hz - self._tone_hz) > TONE_STEP_HZ:
                self._tone_hz, self._tone_ms = hz, FRAME_MS
            else:
                self._tone_ms += FRAME_MS
            return None

        # An observed end verifies the upper duration bound. A gap is ambiguous.
        if self._heard_speech and TONE_MIN_MS <= self._tone_ms <= TONE_MAX_MS:
            self._decision = Decision("voicemail", t_ms, True, "beep_after_speech")
            return self._decision
        self._tone_hz, self._tone_ms = None, 0
        self._speech_ms = self._speech_ms + FRAME_MS if is_active_frame(frame) else 0
        self._heard_speech |= self._speech_ms >= SPEECH_MIN_MS
        return None


class FusionDetector:
    """Fuse v1 audio, transcript cues, and tone evidence for one cutoff."""

    def __init__(
        self,
        audio: Detector | None = None,
        transcript: Detector | None = None,
        tone: Detector | None = None,
        *,
        late_deadline: bool = False,
    ) -> None:
        self._late_deadline = late_deadline
        self._audio = V1Detector() if audio is None else audio
        self._transcript = TranscriptCueDetector() if transcript is None else transcript
        self._tone = ToneDetector() if tone is None else tone

    def start(self) -> DetectorSession:
        return _FusionSession(
            self._audio.start(), self._transcript.start(), self._tone.start(), self._late_deadline,
        )


class _FusionSession(_SignalSession):
    def __init__(
        self, audio: DetectorSession, transcript: DetectorSession, tone: DetectorSession,
        late_deadline: bool = False,
    ) -> None:
        super().__init__()
        self._components = (audio, transcript, tone)
        self._late_deadline = late_deadline
        self._awaiting_late = False
        self._cutoff_ms = 0
        self._seen_ms = 0

    def feed_audio(
        self, frame: bytes, *, t_ms: int, encoding: str = "mulaw8k",
    ) -> Decision | None:
        if not self._closed:
            self._seen_ms = max(self._seen_ms, t_ms)
            for component in self._components:
                component.feed_audio(frame, t_ms=t_ms, encoding=encoding)
            return self._fuse(t_ms)
        return None

    def _feed_audio_at(self, frame: bytes, *, t_ms: int, sample_start_ms: int) -> Decision | None:
        if not self._closed:
            self._seen_ms = max(self._seen_ms, t_ms)
            for component in self._components:
                feed_at = getattr(component, "_feed_audio_at", None)
                if feed_at is None:
                    component.feed_audio(frame, t_ms=t_ms)
                else:
                    feed_at(frame, t_ms=t_ms, sample_start_ms=sample_start_ms)
            return self._fuse(t_ms)
        return None

    def feed_transcript(self, text: str, *, t_ms: int, final: bool) -> Decision | None:
        if not self._closed:
            self._seen_ms = max(self._seen_ms, t_ms)
            for component in self._components:
                feed = getattr(component, "feed_transcript", None)
                if feed is not None:
                    feed(text, t_ms=t_ms, final=final)
            return self._fuse(t_ms)
        return None

    def feed_event(self, name: str, *, t_ms: int) -> Decision | None:
        if self._closed:
            return None
        if self._late_deadline:
            if name == "late_cutoff":
                if not self._awaiting_late or t_ms <= max(self._cutoff_ms, self._seen_ms - 1):
                    return None
                return self._fuse(t_ms, cutoff=True, late=True)
            if name == "cutoff" and self._awaiting_late:
                return None
        for component in self._components:
            component.feed_event(name, t_ms=t_ms)
        if name == "call_completed":
            self._closed = True
            self._decision = Decision("uncertain", t_ms, False, "confirmed_disconnect")
            return self._decision
        return self._fuse(t_ms, cutoff=name == "cutoff")

    def _fuse(self, t_ms: int, *, cutoff: bool = False, late: bool = False) -> Decision | None:
        audio, transcript, tone = (component.decision().label for component in self._components)
        if tone == "voicemail" and transcript != "screener":
            self._decision = Decision("voicemail", t_ms, True, "beep_after_speech")
        elif transcript == "screener" and tone != "voicemail":
            self._decision = Decision("screener", t_ms, True, "screener_phrase")
        elif audio == transcript == "voicemail":
            self._decision = Decision("voicemail", t_ms, True, "audio_transcript_agreement")
        elif cutoff:
            if audio == transcript == "human":
                self._decision = Decision("human", t_ms, True, "audio_transcript_agreement")
            else:
                conflict = len({audio, transcript, tone} - {"uncertain"}) > 1
                reason = "conflicting_signals" if conflict else "insufficient_evidence"
                self._decision = Decision("uncertain", t_ms, False, reason)
                if self._late_deadline and not late:
                    # Stay open after an abstention at cutoff: a later beep or agreeing
                    # phrase can still commit, and ``late_cutoff`` closes the session.
                    self._awaiting_late = True
                    self._cutoff_ms = t_ms
                    return self._decision
        else:
            return None
        self._closed = True
        return self._decision
