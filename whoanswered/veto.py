"""Hold a short-greeting verdict until the two-second cutoff."""

from typing import TYPE_CHECKING

from .baseline import DetectorUpdate, is_active_frame, is_short_greeting_then_pause
from .fixtures import CUTOFF_MS, FRAME_MS

if TYPE_CHECKING:
    from .replay import Observation


class CutoffVeto:
    """Commit to human only with a short greeting and an uninterrupted pause."""

    def __init__(self) -> None:
        self._frames: dict[int, bool] = {}
        self._stream_lost = False
        self._disconnected = False
        self._decided = False

    def __call__(self, observation: "Observation") -> DetectorUpdate | None:
        if self._decided:
            return None

        match observation.event:
            case "start":
                self._stream_lost = False
            case "stop":
                self._stream_lost = True
            case "call_completed":
                self._disconnected = True
            case "media" if not self._stream_lost and not self._disconnected:
                if observation.audio and observation.sample_start_ms is not None:
                    self._frames.setdefault(
                        observation.sample_start_ms, is_active_frame(observation.audio),
                    )
            case "cutoff":
                self._decided = True
                return self._decision()
        return None

    def _decision(self) -> DetectorUpdate:
        if self._disconnected:
            return DetectorUpdate("confirmed_disconnect", "uncertain")

        # A missing frame could hide speech, even after a qualifying pause.
        # Inspect the delivered frames in sample order, regardless of arrival order.
        sample_times = range(0, CUTOFF_MS, FRAME_MS)
        if any(start not in self._frames for start in sample_times):
            return DetectorUpdate("insufficient_evidence", "uncertain")

        greeting_ms = 0
        pause_ms = 0
        for start in sample_times:
            if self._frames[start]:
                if pause_ms:
                    return DetectorUpdate("resumed_speech", "uncertain")
                greeting_ms += FRAME_MS
            elif greeting_ms:
                pause_ms += FRAME_MS

        if is_short_greeting_then_pause(greeting_ms, pause_ms):
            return DetectorUpdate("short_greeting_then_pause", "human")
        if not greeting_ms:
            return DetectorUpdate("connected_silence", "uncertain")
        return DetectorUpdate("insufficient_evidence", "uncertain")
