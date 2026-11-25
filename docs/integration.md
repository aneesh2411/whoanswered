# Integrate WhoAnswered into a voice agent

Install from GitHub with Python 3.12 or later.

```sh
pip install git+https://github.com/aneesh2411/whoanswered
```

The bundled detectors were tuned on authored synthetic audio and, for v3,
authored synthetic transcripts. Real ASR output and real phone audio are
untested.

## Start a session when the call is answered

Create a fresh `V1Detector().start()` session for each call when your telephony
provider reports that the call was answered. Use answer as time zero and send
`session.feed_event("call_answered", t_ms=0)`. Keep the session with the call's
state. When the media stream starts, send `start` with its answer-relative
time. Send `stop` when the media stream stops and `call_completed` when the
provider confirms that the call ended.

## Feed the inbound audio

Listen to the callee's audio. In a Twilio Media Streams websocket handler,
take `message["media"]` from an inbound `media` message and base64-decode its
`payload` to raw mono 8 kHz G.711 mu-law bytes. V1 takes one 20 ms frame
(160 bytes) per `session.feed_audio(frame, t_ms=frame_end_ms)` call. The default
encoding is `mulaw8k`. If your transport batches frames, split the bytes on
20 ms boundaries and advance each frame's timestamp by 20 ms.

Use the frame's **end time** on the answer-relative 20 ms sample grid. For a
Twilio-style `media.timestamp` giving the frame start in milliseconds from
the stream origin, add the stream's answer-relative offset and 20 ms. The
first frame ends at `t_ms=20` when media starts at answer. Establish how the
provider's media clock maps to this sample grid and preserve the timestamps
across gaps. A packet counter or websocket receipt time can hide lost frames
or jitter. Keep missing audio as a gap. V1 rejects unsupported encodings,
frame lengths, and audio timestamps off the grid.

## Route on a commitment or use a fallback

Check updates from `feed_audio` or `feed_event` when they return a `Decision`,
or read the current decision with `session.decision()`. Act once when
`decision.committed` is true. V1 can commit to `human` or `voicemail`. Start
the conversation for `human`, or hand `voicemail` to your mailbox flow and
let that flow decide when it can play a message. A committed decision is
final for the session.

While the decision is `uncertain`, keep listening within your time budget and
hold voicemail playback. At your deadline, deliver `cutoff` after any
eligible inputs you have received. The benchmark uses 2,000 ms after answer.
V1 can report an abstention reason at cutoff and can still commit on later
audio. If your agent has already routed the call, keep that action fixed.
If the call is still uncertain at the deadline, choose a fallback such as a
neutral opening that works for either answerer. A human and a mailbox can
produce identical audio through the deadline, as the benchmark's matched
pair shows. `uncertain` is a valid answer when the evidence supports either
outcome or media is incomplete.

## Run the offline media example

From a repository checkout, run this command at its root with Python 3.12 or
later. The fixture pack is in the checkout.

```sh
python -m examples.media_stream_example
```

The [example](../examples/media_stream_example.py) wraps frames from two
public v1 fixtures in base64 `media.payload` values and passes them through
the same handler shape as a Twilio websocket. It runs offline with no Twilio
dependency. It prints a committed `human` result at 800 ms for the greeting
and an uncommitted `uncertain` result at the cutoff for digital silence.

[Python API reference](api.md) · [Write and score a detector](write-a-detector.md)
