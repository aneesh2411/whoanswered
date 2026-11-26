# WhoAnswered Python API

WhoAnswered requires Python 3.12 or later and uses only the standard library at
runtime. Run `pip install .` from a checkout, or install from GitHub with
`pip install git+https://github.com/aneesh2411/whoanswered`. Both installs
provide the `whoanswered` command and `python -m whoanswered`.

Fixture packs live in the [repository](../fixtures). Use a local pack path with
`validate`, `replay`, `compare`, `evaluate`, or `replay_pack`. The reviewer ZIP
command uses the public v1 pack and supporting files from a checkout.

## Streaming a call

Start one detector session per call. This example sends a synthetic 120 ms
greeting followed by 480 ms of silence and prints `human`.

```python
from whoanswered import V1Detector

detector = V1Detector()
session = detector.start()
session.feed_event("call_answered", t_ms=0)
session.feed_event("start", t_ms=0)

greeting = b"\x00" * 160
silence = b"\xff" * 160
for index, frame in enumerate([greeting] * 6 + [silence] * 24, start=1):
    session.feed_audio(frame, t_ms=index * 20)

result = session.decision()
assert result.committed and result.at_ms == 600
print(result.label)
```

| Import from `whoanswered` | Contract |
| --- | --- |
| `Decision(label, at_ms, committed, reason)` | Frozen dataclass. Labels are `human`, `voicemail`, `screener`, `silence`, and `uncertain`. `at_ms` is relative to answer. `reason` describes the decision. |
| `Detector.start()` | Returns an independent `DetectorSession` with an uncertain decision. |
| `DetectorSession.feed_audio(frame, *, t_ms, encoding="mulaw8k")` | Feeds raw audio and returns a decision update or `None`. |
| `DetectorSession.feed_event(name, *, t_ms)` | Feeds a call event and returns a decision update or `None`. |
| `DetectorSession.feed_transcript(text, *, t_ms, final)` | Optionally consumes a transcript update at its answer-relative arrival time and returns a decision update or `None`. The default ignores it. |
| `DetectorSession.decision()` | Returns the current `Decision`. Its label is `uncertain` until commitment. After commitment, the same decision is final for the session. |
| `V1Detector()` | Uses the frozen v1 audio baseline. It commits to `human` or `voicemail` when its greeting rules match and otherwise remains `uncertain`. |
| `TranscriptCueDetector()` | Looks for phrase cues in finalized transcript updates. |
| `ToneDetector()` | Looks for a steady beep after speech in 160-byte G.711 mu-law frames. |
| `FusionDetector(audio=None, transcript=None, tone=None, *, late_deadline=False)` | Defaults to v1 audio and the bundled transcript and tone detectors. Send `cutoff` after all inputs available at the decision deadline. With `late_deadline=True`, an abstention stays open until a commitment, a later `late_cutoff` event, or `call_completed`. |
| `evaluate(detector, pack_path)` | Starts a session for each case and returns the benchmark scorecard as a dictionary. |
| `replay_pack(detector, pack_path, out)` | Returns the same scorecard and writes the canonical scorecard, traces, and per-case files to `out`. |

Use answer-relative milliseconds. For live v1 audio, feed one 160-byte mono
G.711 mu-law frame at a time with its end time on the 20 ms sample grid. The
first frame ends at `t_ms=20`. Keep the original times across gaps. V1 raises
`ValueError` for unsupported encodings, frame lengths, or audio timestamps.

Event names are strings. Use `call_answered` for answer and, if available,
`start`, `stop`, or `call_completed` for media start, media stop, or confirmed
call completion. Applications can pass their own events such as
`playback_started`. V1 ignores events outside its timing rules. Send `cutoff`
at your decision deadline to get v1's abstention reason if it is still
uncertain. A live session can still commit on subsequent audio. Pack replay
freezes the scored decision at 2,000 ms.

During pack replay, plugins receive each frame's recorded arrival time and
events in replay order, including `cutoff` after all inputs at 2,000 ms. The
built-in V1 adapter also uses the recorded media sample offsets to reproduce
the frozen replay across delayed or reordered frames.

V3 replay calls `feed_transcript` when a session provides it. Send that method
the transcript's arrival time and set `final=True` for a finalized update.
The v3 benchmark transcripts and audio are authored synthetic signals. Real
ASR output and real phone audio are untested in the published benchmark; one
exploratory real call is noted in the signal rules.
[Signal rules](v3-signals.md)

## Loading a detector plugin

Pass a bundled detector name (`v1`, `veto`, `score`, `transcript`, `tone`, or `fusion`) or `module:attribute`
to `--detector` on either `replay` or `compare`. The attribute must be an
importable zero-argument factory that returns a `Detector`. Its `start()`
method must create a fresh session with `feed_audio`, `feed_event`, and
`decision`. `feed_transcript` is optional for existing plugins.
Pass names from code you trust, since Python imports and executes the module.
Make the module available on the Python import path. From the checkout root,
run the [example plugin](../examples/always_uncertain.py) with these commands.

```sh
python -m whoanswered replay --pack fixtures/v1 --out build/plugin-replay --detector examples.always_uncertain:create_detector
python -m whoanswered compare --pack fixtures/v2/eval --out build/plugin-compare --detector examples.always_uncertain:create_detector
```

`compare` includes the v1 reference alongside the selected detector. Without
`--detector`, the v2 report compares `v1`, `veto`, and `score`. The v3 report compares
`v1`, `transcript`, `tone`, and `fusion`. A bad detector
spec exits with status 2 and a `whoanswered: ` message on stderr.

| Problem | Message after the prefix |
| --- | --- |
| Unknown bundled name | `unknown detector '<name>' (choose from v1, veto, score, transcript, tone, fusion)` |
| Malformed plugin spec | `invalid detector spec '<spec>' (expected module:attribute)` |
| Import failed | `cannot import detector module '<module>': <error>` |
| Missing or non-callable attribute | `detector factory '<spec>' is missing or not callable` |
| Factory raised an exception | `detector factory '<spec>' failed: <error>` |
| Factory returned an invalid detector | `detector factory '<spec>' must return a Detector with start()` |
| `start()` raised an exception | `detector '<spec>' could not start a session: <error>` |
| `start()` returned an invalid session | `detector '<spec>' start() must return a DetectorSession` |

## Testing the package

Run `python -m unittest discover -s tests` with pip and setuptools 77 or later
already available in the test interpreter. The package test builds a wheel
offline before installing it in an isolated temporary virtual environment.
