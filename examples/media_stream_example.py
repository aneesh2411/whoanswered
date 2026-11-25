"""Drive a Twilio-style media handler with authored synthetic fixture audio."""

from base64 import b64decode, b64encode
from pathlib import Path

from whoanswered import Decision, DetectorSession, V1Detector
from whoanswered.fixtures import CUTOFF_MS, FRAME_MS, ObservableInput, load_pack


def handle_media(
    session: DetectorSession, media: dict[str, str], *, stream_start_ms: int
) -> Decision | None:
    """Handle the media object from an inbound websocket media message."""
    frame = b64decode(media["payload"], validate=True)
    frame_end_ms = stream_start_ms + int(media["timestamp"]) + FRAME_MS
    return session.feed_audio(frame, t_ms=frame_end_ms, encoding="mulaw8k")


def run_call(observable: ObservableInput) -> Decision:
    """Drive a demo call until commitment or the decision deadline."""
    session = V1Detector().start()
    for event in observable.events:
        if event.arrival_ms > CUTOFF_MS:
            break
        if event.frame_index is None:
            update = session.feed_event(event.event, t_ms=event.arrival_ms)
        else:
            # These demo streams start at answer and deliver each frame on time.
            media = {
                "timestamp": str(event.frame_index * FRAME_MS),
                "payload": b64encode(observable.frame(event.frame_index)).decode("ascii"),
            }
            update = handle_media(session, media, stream_start_ms=0)
        if update is not None and update.committed:
            return update

    session.feed_event("cutoff", t_ms=CUTOFF_MS)
    return session.decision()


def main() -> None:
    pack = Path(__file__).resolve().parents[1] / "fixtures" / "v1"
    cases = {case.case_id: case.observable for case in load_pack(pack)}
    for case_id in ("clean-pickup-hello", "connected-silence-digital"):
        decision = run_call(cases[case_id])
        status = "committed" if decision.committed else "uncommitted"
        print(f"{case_id}: {decision.label} at {decision.at_ms} ms ({status}, {decision.reason})")


if __name__ == "__main__":
    main()
