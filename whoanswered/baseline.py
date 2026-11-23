"""Reference energy and timing rules for 8 kHz G.711 mu-law frames."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .fixtures import FRAME_MS

if TYPE_CHECKING:
    from .replay import Observation


ACTIVE_MEAN_SQUARE = 512**2
HUMAN_MIN_MS = 120
HUMAN_MAX_MS = 360
HUMAN_PAUSE_MS = 480
VOICEMAIL_MIN_MS = 1_000


def decode_mulaw(sample: int) -> int:
    """Decode one mu-law byte to a signed PCM sample."""
    code = sample ^ 0xFF
    exponent = (code >> 4) & 7
    mantissa = code & 15
    magnitude = ((132 + (mantissa << 3)) << exponent) - 132
    return -magnitude if code & 0x80 else magnitude


_SAMPLE_SQUARES = tuple(decode_mulaw(sample) ** 2 for sample in range(256))


def is_active_frame(audio: bytes) -> bool:
    """Apply the v1 energy threshold to a media frame."""
    energy = sum(_SAMPLE_SQUARES[sample] for sample in audio)
    return energy >= ACTIVE_MEAN_SQUARE * len(audio)


def is_short_greeting_then_pause(greeting_ms: int, pause_ms: int) -> bool:
    """Apply the v1 timing rule to a greeting and its following pause."""
    return HUMAN_MIN_MS <= greeting_ms <= HUMAN_MAX_MS and pause_ms >= HUMAN_PAUSE_MS


@dataclass(frozen=True, slots=True)
class DetectorUpdate:
    """A trace reason, optionally accompanied by a verdict."""

    reason: str
    verdict: str | None = None


class AudioBaseline:
    """Commit on a short greeting and pause or a long continuous greeting."""

    def __init__(self) -> None:
        self._pending: dict[int, bool] = {}
        self._next_sample_ms = 0
        self._greeting_ms = 0
        self._pause_ms = 0
        self._resumed = False
        self._committed = False
        self._stream_lost = False
        self._disconnected = False

    def __call__(self, observation: "Observation") -> DetectorUpdate | None:
        match observation.event:
            case "start":
                self._stream_lost = False
            case "stop":
                self._stream_lost = True
                return DetectorUpdate("stream_loss")
            case "call_completed":
                self._disconnected = True
                return DetectorUpdate("confirmed_disconnect")
            case "cutoff" if not self._committed:
                return DetectorUpdate(self._abstention_reason(), "uncertain")
            case "media" if not self._disconnected and not self._stream_lost:
                if observation.audio and observation.sample_start_ms is not None:
                    return self._media(observation.sample_start_ms, observation.audio)
        return None

    def _media(self, sample_start_ms: int, audio: bytes) -> DetectorUpdate | None:
        active = is_active_frame(audio)
        if not self._committed and sample_start_ms >= self._next_sample_ms:
            self._pending.setdefault(sample_start_ms, active)
            update = self._advance_greeting()
            if update is not None:
                self._committed = True
                self._pending.clear()
                return update
        if not active:
            return DetectorUpdate("connected_silence")
        return None

    def _advance_greeting(self) -> DetectorUpdate | None:
        # Read only the contiguous prefix. An unreceived frame cannot count as
        # silence or join two bursts, and repeated frames add no duration.
        while self._next_sample_ms in self._pending:
            active = self._pending.pop(self._next_sample_ms)
            self._next_sample_ms += FRAME_MS
            if self._resumed:
                continue
            if active:
                if self._pause_ms:
                    self._resumed = True
                else:
                    self._greeting_ms += FRAME_MS
                    if self._greeting_ms >= VOICEMAIL_MIN_MS:
                        return DetectorUpdate("long_greeting", "voicemail")
            elif self._greeting_ms:
                self._pause_ms += FRAME_MS
                if is_short_greeting_then_pause(self._greeting_ms, self._pause_ms):
                    return DetectorUpdate("short_greeting_then_pause", "human")
        return None

    def _abstention_reason(self) -> str:
        if self._disconnected:
            return "confirmed_disconnect"
        if self._next_sample_ms == 0 and not self._pending:
            return "missing_media"
        if self._stream_lost:
            return "stream_loss"
        if not self._greeting_ms and not self._pending and self._next_sample_ms >= HUMAN_PAUSE_MS:
            return "connected_silence"
        if self._resumed or (self._greeting_ms > HUMAN_MAX_MS and self._pause_ms):
            return "ambiguous_greeting"
        return "insufficient_evidence"
