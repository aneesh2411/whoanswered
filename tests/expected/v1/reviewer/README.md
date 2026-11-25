# WhoAnswered

[Project site](https://aneesh2411.github.io/whoanswered/)

WhoAnswered is the Two-Second Decision Challenge, an open benchmark for the
first two seconds of an outbound call. It asks who or what answered, when an
agent should commit to a decision, and when it should say it is uncertain.

## Reviewer walkthrough

From the extracted ZIP directory, run the public v1 replay with Python 3.12.
It uses the standard library and CPU, with no network access or credentials.

```sh
python -m whoanswered replay --pack fixtures/v1 --out build/replay
```

Compare `build/replay/scorecard.json` with the bundled
`reference/scorecard.json`. The files should match byte for byte.

1. **Clear human pickup.** Open
   `build/replay/clean-pickup-hello/result.json` for the `human` decision at
   2,000 ms and `build/replay/clean-pickup-hello/trace.json` for the observations
   and detector verdict leading up to it.
2. **Matched-prefix pair.** Compare
   `build/replay/matched-hello-human/trace.json` and
   `build/replay/matched-hello-mailbox/trace.json`. Their observations match
   through 2,000 ms and diverge afterward. Both `initial_verdicts` are
   `uncertain` in the `hello-2000` entry under `matched_prefix_assertions` in
   `build/replay/scorecard.json`.
3. **Simulated screener handoff.** Find `simulated-screener-caller-to-human`
   in `build/replay/traces.jsonl`. Its `actions` show
   `reply_identity_and_purpose` at 1,020 ms and `start_conversation` at
   2,420 ms after human takeover. Its `initial_decision` records `uncertain`
   at 2,000 ms. The same case appears under `policy_assertions` in
   `build/replay/scorecard.json`.

## Build and verify the reviewer ZIP

Run from the repository root with Python 3.12.

```sh
python -m whoanswered bundle --pack fixtures/v1 --out build/whoanswered-v1.zip
python -m unittest tests.test_release
```

The tests verify byte stability and compare replay outputs from a clean
extraction with the committed scorecard and traces, including a replay with
socket operations denied.

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

Run these commands from the repository root.

```sh
python -m whoanswered validate --pack fixtures/v1
python -m whoanswered replay --pack fixtures/v1 --out build/replay
python -m unittest tests.test_challenge_pack tests.test_scorecard
```

Replay uses the frozen `v1` detector by default. Use `--detector v1` to select
it explicitly.

The replay writes `build/replay/scorecard.json` and
`build/replay/traces.jsonl`. The scorecard reports committed coverage across
acoustic fixtures and errors among those commitments. It measures wrong
voicemail routing across all human fixtures and reports the scripted policy
assertions separately. Each trace keeps the initial verdict at 2,000 ms and
records subsequent observations and playback actions at their own times.

[Challenge pack and generator notes](fixtures/v1/README.md)

## v2 development and reserved evaluation packs

The revision 2 development and reserved evaluation packs each contain 20
scenarios with authored synthetic audio. The `eval` split was authored
alongside `dev`. Transfer to real phone audio is untested. Use `dev` for
detector development and keep `eval` frozen for final comparison after the
detector and configuration are fixed.

```sh
python -m whoanswered validate --pack fixtures/v2/dev --pack fixtures/v2/eval
python -m unittest discover -s tests
```

[Case matrix, generation, and license](fixtures/v2/README.md)

Run development selection with
`python -m whoanswered tune --pack fixtures/v2/dev --out build/tune`.
It writes `build/tune/selection.json` and requires a dev-only revision 2 pack.
[Development results and frozen operating point](docs/v2-dev-results.md)

## Compare bundled detectors

The `veto` detector applies v1's short-greeting rule at 2,000 ms. It commits to
`human` when a short greeting is followed by a qualifying pause with complete
media through the cutoff. Resumed speech and missing frames cause abstentions.
The cutoff observation in the replay trace records the decision and its reason.

```sh
python -m whoanswered replay --pack fixtures/v2/dev --detector veto --out build/veto
python -m whoanswered compare --pack fixtures/v2/eval --policy-pack fixtures/v1 --out build/compare
cmp docs/v2-eval-comparison.json build/compare/comparison.json
```

The offline comparison uses Python 3.12 and the 2,000 ms cutoff for all three
detectors. The report pins each pack by its manifest SHA-256, which covers the
payload hashes. It records the thresholds and identifies detector versions by
SHA-256 digests of the Python sources with LF line endings. The EVAL
matched-prefix pair passed for all three detectors. All six public v1 scripted
policy cases passed the separate policy gate.

| Detector | Committed coverage | Correct / eligible | Wrong commitments | Human as mailbox | Mailbox as human | Median commitment |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1 | 10/18 | 6/18 | 4 | 0/10 | 4/8 | 900 ms |
| Veto | 6/18 | 3/18 | 3 | 0/10 | 3/8 | 2,000 ms |
| Score, 12 frames | 6/18 | 3/18 | 3 | 0/10 | 3/8 | 2,000 ms |

**v1 stays as the reference.** Neither candidate met the promotion rule
because both fell below v1's coverage.
[Published EVAL report](docs/v2-eval-comparison.json) ·
[Revision 2 evaluation rules](docs/fixture-format.md#revision-2-evaluation-rules)

## License

[MIT](LICENSE) · [Fixture license](fixtures/LICENSE.md)
