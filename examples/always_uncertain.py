"""A minimal detector factory for offline replay and comparison."""

from whoanswered import Decision, Detector, DetectorSession


class _Session:
    def feed_audio(self, frame: bytes, *, t_ms: int, encoding: str = "mulaw8k") -> Decision | None:
        return None

    def feed_event(self, name: str, *, t_ms: int) -> Decision | None:
        return None

    def decision(self) -> Decision:
        return Decision("uncertain", 0, False, "always_uncertain")


class _Detector:
    def start(self) -> DetectorSession:
        return _Session()


def create_detector() -> Detector:
    """Create a detector that abstains on every call."""
    return _Detector()
