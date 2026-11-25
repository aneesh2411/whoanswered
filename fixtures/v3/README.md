# v3 fixture packs

The dev and reserved eval packs each contain 20 authored scenarios with
synthetic audio and synthetic transcripts. Real ASR output and real phone audio
are untested. The transcript arrival times, final flags, and recognition
errors are scripted. Both splits share recipes for the scenario families and
use distinct synthetic sources, tone frequencies, and transcript variants.

| Cases per split | Scenario family |
| ---: | --- |
| 4 | Audio and transcript agreement opportunities, including a final at 2,000 ms |
| 3 | Interim correction, wrong final, and an empty update |
| 3 | Disagreement between a finalized transcript and the audio cues |
| 1 | Brief tone on a human call |
| 2 | Mailbox beeps with and without a matching transcript |
| 2 | A beep or transcript arriving after cutoff |
| 1 | Screener prompt |
| 4 | Two human/mailbox pairs with identical delivered audio and transcript events through cutoff |

Use `dev` for tuning. Both manifests were pinned before tuning, and the
reserved comparison used the frozen `eval` split after the settings were fixed.

```sh
python -m fixtures.generate_v3 --out build/v3
python -m whoanswered validate --pack fixtures/v3/dev --pack fixtures/v3/eval
```

[Fixture format](../../docs/fixture-format.md) ·
[Detector rules](../../docs/v3-signals.md) · [MIT fixture license](../LICENSE.md)
