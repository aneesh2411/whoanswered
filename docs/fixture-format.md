# Fixture pack format

A pack is a local directory with a `manifest.json` at its root. Run
`python -m whoanswered validate --pack fixtures/smoke` to check the committed
example. Validation exits zero on success and prints a stable error code and
location to stderr on failure. Replay validates the entire pack before it
writes any results.

## Manifest

`manifest.json` contains the JSON integers `schema_version` and
`pack_version`. The fixture schema is `1` for both supported pack revisions
(`1` and `2`). `cases` is a nonempty array sorted by `id` in lexicographic
order.

| Case field | Meaning |
| --- | --- |
| `id` | Unique case ID using ASCII letters, digits, hyphens, or underscores and starting with a letter or digit. IDs that differ only in capitalization collide. |
| `track` | Nonempty benchmark track name, such as `acoustic`. |
| `source_group` | Nonempty identifier shared by cases derived from the same source. |
| `provenance` | Nonempty description of where the case data came from or how it was generated. |
| `license` | Nonempty license identifier or reference, such as `MIT`. |
| `split` | Required in pack revision `2`. Use `dev` for development or `eval` for reserved evaluation. Revision `1` cases load with no split. |
| `family` | Optional nonempty fixture family name in pack revision `2`. The comparison groups cases without a family under `unspecified`. |
| `payloads` | Object with entries named `audio.ulaw`, `events.jsonl`, and `expected.json`. Each entry contains `path` and `sha256`. |
| `policy_events` | Optional list for the `policy` track. Each record has a nonnegative, answer-relative integer `at_ms` and an `event` of `screener`, `prompt_end`, `human_takeover`, `mailbox_ready`, or `disconnect`. |

Each payload `path` is relative to the pack root, uses `/` separators, and ends
with the corresponding filename. Paths with empty, `.` or `..` components,
backslashes, colons, or symlink targets outside the pack are invalid. The
`sha256` field is a 64-character lowercase hexadecimal digest of the payload's
exact on-disk bytes. Recompute the digest after editing a payload.

## Audio

`audio.ulaw` is raw, mono G.711 mu-law at 8,000 samples per second, with one
byte per sample and no container header. A 20 ms frame occupies 160 bytes.
Frame indices start at zero. Frame `n` occupies bytes `[160*n, 160*(n+1))`
and represents samples in `[20*n, 20*(n+1))` milliseconds after answer.
The file must contain whole frames. An empty file represents a case with no
recorded audio frames.

## Events

`events.jsonl` stores scripted local events using Twilio Media Streams event
names for the stream lifecycle and a nested `media` object for frame references.
Every record has `event`, `arrival_ms`, and `sequence`. `arrival_ms` and
`sequence` are nonnegative JSON integers. Sequence numbers are unique within
a case. Store records in increasing `(arrival_ms, sequence)` order, using
`sequence` to break ties.

| Event | Meaning and fields |
| --- | --- |
| `call_answered` | Local answer marker. It occurs exactly once at `arrival_ms: 0` and is the first record. |
| `connected` | The media transport connected. |
| `start` | The media stream started. |
| `media` | A frame arrived. This record also has `media: {"frame_index": n}`, where `n` is a nonnegative integer less than the number of audio frames. Arrival time is recorded separately from the frame's sample time. |
| `stop` | The media stream stopped. |
| `call_completed` | Local signaling confirmed that the call ended. |

```jsonl
{"arrival_ms":0,"event":"call_answered","sequence":1}
{"arrival_ms":0,"event":"start","sequence":2}
{"arrival_ms":20,"event":"media","media":{"frame_index":0},"sequence":3}
```

## Evaluator assertions

Each `expected.json` contains these fields.

| Field | Meaning |
| --- | --- |
| `answerer` | Nonempty label for the eventual ground truth. |
| `matched_pair` | `null` for an unpaired case, or a nonempty label shared by exactly two cases in the pack. |
| `verdict` | Nonempty label for the expected initial verdict, such as `uncertain`. |
| `actions` | Ordered list of expected actions. Each entry contains a nonempty `action` label and a nonnegative integer `at_ms` relative to answer. Entries are ordered by `at_ms`, with list order breaking ties. An empty list asserts zero actions. |

`whoanswered.fixtures.load_pack(Path(...))` returns cases in manifest order.
Each case exposes audio bytes and ordered events through `.observable`, and
answerer, pair membership, verdict, and actions through `.expected`. Split
membership and the rest of the case metadata live on the enclosing case.
Replay passes `.observable` to the case runner.

## Replay and cutoff

Virtual time starts at `call_answered`. Replay delivers observations to a fresh
detector in `(arrival_ms, sequence)` order. Each observation carries its event
and answer-relative time. Media observations carry the sample start time and
one frame of audio. Replay keeps the pack metadata, evaluator assertions, and
full audio file outside the detector interface.

The detector receives the `cutoff` observation at 2,000 ms, after the eligible
inputs at that time. A frame can affect the initial decision if it arrived by
2,000 ms and all its samples fit in `[0, 2000)` ms. This includes a frame
ending at 2,000 ms that arrives at that instant. A frame whose samples extend
beyond the decision window is discarded if its recorded arrival is at or
before the cutoff. File exhaustion and `stop` leave the cutoff timer running.
Only `call_completed` signals confirmed call completion.

The first detector commitment through the cutoff becomes the initial decision,
with `uncertain` as the default if the detector has not committed. Replay
continues to deliver arrivals after the cutoff. `result.json` records the
initial decision at 2,000 ms, and `trace.json` lists observations in delivery
order, including any later verdicts. Both files go directly under `--out`
for a single case, or under one subdirectory per case ID for a pack with
multiple cases.

Replay also writes `scorecard.json` and `traces.jsonl` directly under
`--out`. The JSONL file has one record per case in manifest order, with its
frozen `initial_decision`, detector `observations`, injected `policy_events`,
and timestamped playback `actions`. The playback policy uses the injected
events and records its actions in an in-memory sink. At equal timestamps,
disconnect has priority and the remaining events retain their script order.

The scorecard records a numerator, denominator, and rate for each metric.

- Committed coverage divides initial verdicts other than `uncertain` by
  all acoustic cases.
- Committed error divides commitments that disagree with the eventual
  answerer by all acoustic commitments.
- Wrong voicemail routing divides human cases with any `play_voicemail`
  action by all cases with a human answerer, including the policy track.

A zero denominator produces a JSON `null` rate. Matched-prefix assertions
check that both cases have identical detector inputs through the cutoff and
an `uncertain` initial verdict. Policy assertions compare the exact sequence
and timestamps of actual and expected actions for each policy case. Injected
events are delivered only to the playback policy. Acoustic recognition metrics
use only the acoustic track.

## Revision 2 evaluation rules

### Development and reserved evaluation

Use `dev` to design the detector, select thresholds, and diagnose mistakes.
Freeze split assignments, case membership, assertions, and payload digests
before tuning. Use `eval` for the final comparison after freezing the candidate
detector and configuration. Keep sources used in development, including any
reused public v1 material, out of reserved evaluation. Reserve a fresh
evaluation set for any tuning informed by an earlier evaluation.

Put each source group and all its variants in one split, even when timing,
noise, media gaps, or eventual outcomes differ. Keep both members of a matched
pair in the same pack and split. Copied audio openings also belong in one
split, even if the fixtures have different group IDs.

Validation checks the first non-silent 20 ms frame of the stored audio and
the first non-silent frame in delivery order through the cutoff. Sharing
either opening across splits is a validation error, even
when playback timing or the following audio differs. The check ignores
leading digital silence and treats the two mu-law zero codes alike. Empty
and entirely digital-silent openings can be shared. Use the same source
group for transformed copies whose frame bytes differ.

`load_pack` checks split isolation within a revision 2 pack. If dev and eval
live in separate directories, validate both together before freezing them.

```sh
python -m whoanswered validate --pack path/to/dev --pack path/to/eval
```

### Eligibility, reporting, and promotion

Use the same frozen reserved `eval` cases and the same 2,000 ms cutoff for
both the `v1` detector and the candidate. Eligible cases are unpaired
`acoustic` fixtures whose eventual answerer is `human` or `voicemail`.
Matched-prefix assertions and scripted policy checks must pass as separate
gates. Cases with other truth labels, including silence, unknown answerers, and
disconnects, are outside this denominator. Include every eligible case
even when the audio is quiet, incomplete, or ambiguous. Select eligible
cases before replay and use the same denominator for both detectors.

For each detector, the comparison report must print committed coverage
(commitments divided by all eligible cases), committed error (wrong
commitments divided by commitments), and correct-over-all-eligible
(correct commitments divided by all eligible cases). Count a frozen
verdict other than `uncertain` as a commitment and compare it to the
eventual answerer. Report human-as-mailbox and mailbox-as-human separately,
using all eligible cases of the respective true class as the denominator.
Print the median commitment time in milliseconds from answer, using the
observation time of the verdict that froze each commitment, including wrong
ones. Average the middle two times for an even count. Report `null` for
rates with zero denominators and for the median when there are no commitments.
Report abstentions by true class and fixture family, with the number of
eligible cases in each group as its denominator.

Promote the candidate only if coverage is at least `v1` coverage, neither
human-as-mailbox nor mailbox-as-human errors increase, and correct
commitments strictly increase or wrong commitments strictly decrease.
Compare the unrounded counts on the common eligible set. Keep `v1` when
the candidate misses the rule. The revision 1 replay scorecard retains its
original metrics and denominators.

## Canonical bytes

Use UTF-8 without a BOM and LF line endings, including a final LF. Sort object
keys lexicographically at every level. Use two-space indentation for JSON.
In JSONL, write one compact object per line without spaces between tokens or
blank lines. Preserve non-ASCII characters in UTF-8. Duplicate keys and
non-finite JSON numbers are invalid. `canonical_json` and `canonical_jsonl`
in `whoanswered.fixtures` emit these encodings. Arrange arrays and event
records in their specified order before encoding.
