# Revision 2 fixture packs

Each split has 20 acoustic cases across six source groups. Every variant stays
with its parent source, including changes to timing, room tone, frame delivery,
and the eventual answerer. Each split uses its own authored source recipes and
fixed seeds.

| Family | Cases per split | Variants |
| --- | ---: | --- |
| Short human | 2 | Clean pickup and pickup with room tone |
| Short mailbox | 2 | Recording tone and room tone without a recording tone |
| Long human | 2 | Speech across cutoff, with full delivery or missing frames |
| Resumed human | 6 | Before, near, and after cutoff, each with a clean control and a delivery gap or room tone variant |
| Paused mailbox | 6 | Before, near, and after cutoff, each with a clean control and a delivery gap or room tone variant |
| Matched prefix | 2 | Identical observations through cutoff, followed by a human or mailbox continuation |

Continuation times are relative to answer. Frames arrive when their 20 ms of
audio ends. In the near cases, the first continuation frame arrives at cutoff.
The delivery-gap variants omit frames during the pause. The room-tone variants
fill the pause with seeded noise.

| Split | Before cutoff | Near cutoff | After cutoff | Matched tails start |
| --- | ---: | ---: | ---: | ---: |
| `dev` | 980 ms | 1,980 ms | 2,240 ms | 2,180 ms |
| `eval` | 1,140 ms | 1,980 ms | 2,320 ms | 2,320 ms |

Each matched pair expects `uncertain` at 2,000 ms. Different continuation audio
reveals the human and mailbox outcomes after cutoff. The tests compare the
actual detector inputs through cutoff and check where the audio first differs.

## Generation and provenance

All audio is first-party synthesis from authored phonetic scripts, integer
formant synthesis, seeded noise, and generated tones. The generator reuses
the v1 synthesis and event-writing routines with new source recipes. The packs
remain synthetic. They contain zero recordings of real people and zero
third-party audio. Transfer to real phone audio is untested.

Run from the repository root with Python 3.12. Generation and validation use
the standard library and CPU and run offline.

```sh
python -m fixtures.generate_v2 --out build/regenerated-v2
python -m whoanswered validate --pack fixtures/v2/dev --pack fixtures/v2/eval
python -m unittest tests.test_v2_packs
```

The tests pin both manifest hashes and verify that every payload regenerates
byte for byte. The reserved manifest fixes split and source-group membership,
case IDs, and payload hashes before detector tuning. Use `dev` for tuning and
reserve `eval` for comparison with a frozen candidate.
[Revision 2 evaluation rules](../../docs/fixture-format.md#revision-2-evaluation-rules)

## License

The generated audio, event scripts, assertions, manifests, and generator are
available under the MIT License. [Fixture license](../LICENSE.md)
