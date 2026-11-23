# Public challenge pack v1

This pack contains 29 first-party scenarios. The acoustic track covers clean
pickups, short and paused mailbox greetings, connected silence, media loss,
and confirmed disconnect. The signature pair shares a synthesized “Hello?”
and the same detector observations through 2,000 ms. One tail continues as a
human pickup. The other continues as a mailbox greeting.

The policy track contains six **simulated generic screener** cases. All audio
is generated locally from authored phonetic scripts and signals. The pack has
zero captured vendor prompts, real calls, or recorded voices.

Run from the repository root with Python 3.12.

```sh
python -m whoanswered validate --pack fixtures/v1
python -m unittest tests.test_challenge_pack
```

## Generation and provenance

[The generator](../generate_v1.py) renders speech-like signals with
hand-authored phone timings, integer formant synthesis, and seeded breath
noise. It also generates digital silence, low-level room tone, and generic
recording tones. It encodes all audio as raw 8 kHz G.711 mu-law and scripts
frame arrivals and call signaling separately. It runs on CPU with the Python
standard library.

The manifest gives each source group a fixed `seed` and identifies each case's
`family`. A group contains the variants of a single source recipe. Utterances
use the source seed plus their zero-based position in that recipe. Variants
reuse their source even when they shift timing, change transport behavior, or
continue to different outcomes. Keep a source group together when splitting
cases for evaluation.

Generate a copy in a separate directory with the following command. The test
suite verifies that regeneration produces the committed bytes.

```sh
python -m fixtures.generate_v1 --out build/regenerated-v1
```

## Evaluator assertions

The eventual answerer and the intended initial verdict are recorded separately
in each `expected.json`. For example, the paired cases expect `uncertain` at
2,000 ms and have different eventual answerers. The reference energy baseline
can disagree with an assertion, including when it mistakes a paused mailbox
greeting for a pickup. [Fixture format](../../docs/fixture-format.md)

Each policy case has `simulated: true` and scripted `policy_events` in the
manifest. These answer-relative events are inputs to
`whoanswered.policy.run_policy`, which the tests exercise against the expected
actions. The acoustic replay reads the separate `events.jsonl` transport
and media stream.

All fixture content in this pack is available under the MIT license.
[Fixture license](../LICENSE.md)
