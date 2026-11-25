"""Resolve CLI detector names and importable factory specs."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module

from ._detector import Detector, DetectorSession
from .comparison import DETECTOR_FACTORIES
from .replay import Detector as ReplayDetector, _streaming_replay


class DetectorSpecError(ValueError):
    """A detector spec could not be loaded or used to start a session."""


def load_detector_factory(spec: str) -> Callable[[], ReplayDetector]:
    if ":" not in spec:
        if spec in DETECTOR_FACTORIES:
            return DETECTOR_FACTORIES[spec]
        available = ", ".join(DETECTOR_FACTORIES)
        raise DetectorSpecError(f"unknown detector {spec!r} (choose from {available})")

    module, attribute = spec.split(":", 1)
    if not attribute.isidentifier() or not all(part.isidentifier() for part in module.split(".")):
        raise DetectorSpecError(f"invalid detector spec {spec!r} (expected module:attribute)")
    try:
        plugin = import_module(module)
    except Exception as error:
        raise DetectorSpecError(f"cannot import detector module {module!r}: {error}") from error
    factory = getattr(plugin, attribute, None)
    if not callable(factory):
        raise DetectorSpecError(f"detector factory {spec!r} is missing or not callable")
    try:
        detector = factory()
    except Exception as error:
        raise DetectorSpecError(f"detector factory {spec!r} failed: {error}") from error
    if not callable(getattr(detector, "start", None)):
        raise DetectorSpecError(f"detector factory {spec!r} must return a Detector with start()")
    checked = _PluginDetector(spec, detector)
    return lambda: _streaming_replay(checked)


@dataclass(frozen=True)
class _PluginDetector:
    spec: str
    detector: Detector

    def start(self) -> DetectorSession:
        try:
            session = self.detector.start()
        except Exception as error:
            raise DetectorSpecError(f"detector {self.spec!r} could not start a session: {error}") from error
        if not all(callable(getattr(session, method, None)) for method in ("feed_audio", "feed_event", "decision")):
            raise DetectorSpecError(f"detector {self.spec!r} start() must return a DetectorSession")
        return session
