"""The streaming detector interfaces and the frozen v1 implementation."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, cast

from .baseline import AudioBaseline, DetectorUpdate
from .fixtures import FRAME_BYTES, FRAME_MS

if TYPE_CHECKING:
    from .replay import Observation


type _Label = Literal["human", "voicemail", "screener", "silence", "uncertain"]


@dataclass(frozen=True)
class Decision:
    """A label, answer-relative timestamp, commitment flag, and reason.

    The label stays ``uncertain`` until commitment. The timestamp and reason
    of a committed decision stay fixed for the rest of the call.
    """

    label: _Label
    at_ms: int
    committed: bool
    reason: str


class Detector(Protocol):
    """A detector that creates independent sessions for calls."""

    def start(self) -> "DetectorSession":
        """Start a fresh session with an uncertain decision."""
        ...


class DetectorSession(Protocol):
    """A call's streaming state. Once committed, its decision is final."""

    def feed_audio(
        self, frame: bytes, *, t_ms: int, encoding: str = "mulaw8k"
    ) -> Decision | None:
        """Consume an audio frame available at answer-relative ``t_ms``.

        Return a decision when it changes, or ``None``.
        """
        ...

    def feed_event(self, name: str, *, t_ms: int) -> Decision | None:
        """Consume a call event at answer-relative ``t_ms`` and return any change."""
        ...

    def feed_transcript(self, text: str, *, t_ms: int, final: bool) -> Decision | None:
        """Optionally consume a transcript update at answer-relative arrival time."""
        return None

    def decision(self) -> Decision:
        """Return the current decision, which is uncertain until commitment."""
        ...


class V1Detector:
    """The frozen v1 baseline for 20 ms, 8 kHz G.711 mu-law frames."""

    def start(self) -> DetectorSession:
        """Start a v1 call session with audio frame times on the 20 ms grid."""
        return _V1Session()


class _V1Session(DetectorSession):
    def __init__(self) -> None:
        self._baseline = AudioBaseline()
        self._decision = Decision("uncertain", 0, False, "insufficient_evidence")

    def decision(self) -> Decision:
        return self._decision

    def feed_audio(
        self, frame: bytes, *, t_ms: int, encoding: str = "mulaw8k"
    ) -> Decision | None:
        if encoding != "mulaw8k":
            raise ValueError(f"v1 does not support audio encoding {encoding!r}")
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"v1 requires {FRAME_BYTES}-byte audio frames")
        if t_ms < FRAME_MS or t_ms % FRAME_MS:
            raise ValueError(f"v1 audio t_ms must be a positive multiple of {FRAME_MS}")
        return self._feed_audio_at(frame, t_ms=t_ms, sample_start_ms=t_ms - FRAME_MS)

    def _feed_audio_at(self, frame: bytes, *, t_ms: int, sample_start_ms: int) -> Decision | None:
        from .replay import Observation

        return self._feed(Observation("media", t_ms, sample_start_ms=sample_start_ms, audio=frame))

    def feed_event(self, name: str, *, t_ms: int) -> Decision | None:
        from .replay import Observation

        return self._feed(Observation(name, t_ms))

    def _feed(self, observation: "Observation") -> Decision | None:
        previous = self._decision
        self._observe(observation)
        return self._decision if self._decision != previous else None

    def _observe(self, observation: "Observation") -> DetectorUpdate | None:
        # Fixture replay also preserves the original sample offsets and trace reasons.
        update = self._baseline(observation)
        if not self._decision.committed and update is not None and update.verdict is not None:
            label = cast(_Label, update.verdict)
            self._decision = Decision(label, observation.at_ms, label != "uncertain", update.reason)
        return update
