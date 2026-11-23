"""Run scripted playback decisions for the policy-only benchmark track."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Literal


@dataclass(frozen=True, slots=True)
class PolicyEvent:
    """An injected signal at an answer-relative time. Disconnect is confirmed."""

    event: Literal["screener", "prompt_end", "human_takeover", "mailbox_ready", "disconnect"]
    at_ms: int


@dataclass(frozen=True, slots=True)
class PlaybackAction:
    action: str
    at_ms: int


class InMemoryPlaybackSink:
    """Record playback requests and keep them pending until cancellation."""

    def __init__(self) -> None:
        self.trace: list[PlaybackAction] = []
        self.pending: list[PlaybackAction] = []

    def play(self, action: str, at_ms: int) -> None:
        request = PlaybackAction(action, at_ms)
        self.pending.append(request)
        self.trace.append(request)

    def cancel(self, at_ms: int) -> None:
        self.pending.clear()
        self.trace.append(PlaybackAction("cancel_playback", at_ms))


class _State(Enum):
    WAITING = auto()
    SCREENING = auto()
    REPLIED = auto()
    HUMAN = auto()
    VOICEMAIL = auto()


def run_policy(events: Iterable[PolicyEvent], sink: InMemoryPlaybackSink) -> None:
    """Run one call with a fresh sink and prioritize disconnect at timestamp ties.

    Other events at the same time retain script order. The first human takeover
    or ready mailbox selects the call's destination.
    """
    state = _State.WAITING
    for event in sorted(events, key=lambda event: (event.at_ms, event.event != "disconnect")):
        if event.event == "disconnect":
            sink.cancel(event.at_ms)
            return
        if state in (_State.HUMAN, _State.VOICEMAIL):
            continue

        if event.event == "screener" and state is _State.WAITING:
            state = _State.SCREENING
        elif event.event == "prompt_end" and state is _State.SCREENING:
            state = _State.REPLIED
            sink.play("reply_identity_and_purpose", event.at_ms)
        elif event.event == "human_takeover":
            state = _State.HUMAN
            sink.play("start_conversation", event.at_ms)
        elif event.event == "mailbox_ready":
            state = _State.VOICEMAIL
            sink.play("play_voicemail", event.at_ms)
