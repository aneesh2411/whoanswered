# Write and score your own detector

Start by copying [the always-uncertain example](../examples/always_uncertain.py)
to a module on your Python import path. The example returns
`Decision("uncertain", 0, False, "always_uncertain")` for every call, so you
can check factory loading and replay before adding detection logic.

## Implement the session and factory

1. Export a zero-argument factory, such as `create_detector()`, that returns
   an object implementing `Detector` from `whoanswered`.
2. Implement `start()` to return a fresh `DetectorSession`. Keep audio history
   and decisions in that session so calls stay independent.
3. Implement `feed_audio(frame, *, t_ms, encoding="mulaw8k")`,
   `feed_event(name, *, t_ms)`, and `decision()`. Return a `Decision` from a
   feed method when the decision changes and `None` when it stays the same.
   `decision()` always returns the current state. Keep the label `uncertain`
   and `committed=False` until the evidence justifies a label. On commitment,
   save the label, answer-relative time, and reason, and return that same
   decision for the rest of the call.

Replay gives plugins `t_ms` as the recorded arrival time and delivers events
in replay order. At 2,000 ms it delivers `cutoff` after the eligible inputs
and freezes the scored decision. The session receives the audio and call
events available at that point. Use `cutoff` to make a deadline-based
decision or record why the session abstained.

## Replay and compare

Use `--detector module:Factory`, where `module` is importable and `Factory`
is the name of your zero-argument callable. Run the following baseline from
the repository root with Python 3.12 or later. Replace
`examples.always_uncertain:create_detector` with your own factory spec once
it is importable.

```sh
python -m whoanswered replay --pack fixtures/v2/dev --out build/plugin-replay --detector examples.always_uncertain:create_detector
python -m whoanswered compare --pack fixtures/v2/eval --out build/plugin-compare --detector examples.always_uncertain:create_detector
```

Use development replay's `scorecard.json` and `traces.jsonl` to inspect
coverage, errors, and the input that led to each decision. Once the detector
and its settings are fixed, run `compare` on the reserved evaluation split.
`comparison.json` reports the plugin alongside the `v1` reference, including
correct and wrong commitments, abstentions, and commitment timing. The
always-uncertain example has zero committed coverage. All fixture audio is
authored synthetic audio. Real phone audio is untested.

[Python API and plugin errors](api.md) ·
[Evaluation rules](fixture-format.md#revision-2-evaluation-rules)
