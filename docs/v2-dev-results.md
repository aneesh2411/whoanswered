# Revision 2 development results

The pinned `fixtures/v2/dev/manifest.json` has SHA-256
`e08648ebe8497c24eed38eb2dd05d9bfe728ad8461d1fc2176b060b0c42c4a36`.
The comparison uses 18 eligible acoustic cases. The two matched-prefix cases
are checked separately. All three detectors abstained on them.

```sh
python -m whoanswered tune --pack fixtures/v2/dev --out build/tune
```

| Detector | Committed coverage | Correct commitments | Errors among commitments |
| --- | ---: | ---: | ---: |
| v1 | 10/18 | 6 | 4/10 |
| Veto | 4/18 | 3 | 1/4 |
| Score, 12 frames | 5/18 | 4 | 1/5 |

Every error was a mailbox classified as human. The veto introduced zero errors.

| DEV family | Eligible cases | Correct v1 commitments lost by veto | v1 errors avoided by veto |
| --- | ---: | ---: | ---: |
| Long human | 2 | 0 | 0 |
| Paused mailbox | 6 | 0 | 3 |
| Resumed human | 6 | 3 | 0 |
| Short human | 2 | 0 | 0 |
| Short mailbox | 2 | 0 | 0 |

The veto dropped three correct resumed-human commitments. In
`dev-resumed-human-before`, the resumed speech ended with 320 ms of quiet
before cutoff. The score recovered that case and preserved the veto's three
avoided errors. Both near-cutoff human resumptions still abstained because
they had no trailing quiet. The 6- and 12-frame thresholds produced the same
counts. The 24-frame threshold recovered no commitments. The matched-prefix
pair passed at all three thresholds.

**Frozen candidate** is `score` (`replay --detector score`). It decides at
2,000 ms using complete 20 ms frames and a mean-square PCM activity threshold
of 262,144. It keeps the veto's inclusive 120–360 ms opening greeting and
minimum 480 ms opening pause. When speech resumes after that pause, the
score is the smaller of the number of active frames from resumption onward
and the number of consecutive quiet frames at cutoff. The detector commits
to human at a score of **12 frames or more**.

Selection requires a gain in correct commitments over the veto, preservation
of its correct commitments, zero added errors, and passing matched-prefix
checks. It maximizes the gain and picks the higher threshold on a tie. The
veto is the fallback. The selected score still trails v1 in DEV coverage.
