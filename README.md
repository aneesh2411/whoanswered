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
python -m unittest tests.test_fixtures tests.test_smoke
```

The smoke pack contains two frames of synthetic silence and scripted call
events. Replay validates the pack and writes `build/smoke/result.json` with a
provisional `uncertain` decision and `build/smoke/trace.json` with the audio's
SHA-256 and the reason for the decision. See [Fixture pack format](docs/fixture-format.md).

The smoke test pins the fixture bytes and compares both output files byte for
byte with the committed expectations in `tests/expected/smoke/`.

## License

[MIT](LICENSE)
