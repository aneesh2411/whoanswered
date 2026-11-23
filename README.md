# WhoAnswered

WhoAnswered is the Two-Second Decision Challenge, an open benchmark for the
first two seconds of an outbound call. It asks who or what answered, when an
agent should commit to a decision, and when it should say it is uncertain.

## Run the smoke replay

Run these commands from the repository root with Python 3.12. The replay uses
the standard library, runs on CPU, and requires no installation or network
access.

```sh
python -m whoanswered --help
python -m whoanswered validate --pack fixtures/smoke
python -m whoanswered replay --pack fixtures/smoke --out build/smoke
python -m unittest tests.test_fixtures tests.test_replay tests.test_smoke
```

The smoke pack contains two frames of synthetic silence and scripted call
events. Replay validates the pack and writes `build/smoke/result.json` with
the provisional `uncertain` decision frozen at 2,000 ms after answer. It
records the delivered observations, including per-frame SHA-256 digests, in
`build/smoke/trace.json`. [Fixture pack format](docs/fixture-format.md)

The smoke test pins the fixture bytes and compares both output files byte for
byte with the committed expectations in `tests/expected/smoke/`.

## Public challenge pack

`fixtures/v1` contains 29 synthetic scenarios, including a matched human and
mailbox pair with identical observations through 2,000 ms. It also includes
simulated screener transitions for the policy track.

```sh
python -m whoanswered validate --pack fixtures/v1
python -m whoanswered replay --pack fixtures/v1 --out build/replay
python -m unittest tests.test_challenge_pack tests.test_scorecard
```

The replay writes `build/replay/scorecard.json` and
`build/replay/traces.jsonl`. The scorecard reports committed coverage across
acoustic fixtures and errors among those commitments. It measures wrong
voicemail routing across all human fixtures and reports the scripted policy
assertions separately. Each trace keeps the initial verdict at 2,000 ms and
records subsequent observations and playback actions at their own times.

[Challenge pack and generator notes](fixtures/v1/README.md)

## License

[MIT](LICENSE)
