# Fixture pack format

A pack is a local directory with a `manifest.json` at its root. Run
`python -m whoanswered validate --pack fixtures/smoke` to check the committed
example. Validation exits zero on success and prints a stable error code and
location to stderr on failure. Replay validates the entire pack before it
writes any results.

## Manifest

`manifest.json` contains `schema_version` and `pack_version`, both JSON
integers set to `1` for this format. The schema version identifies the file
contract. The pack version identifies the supported pack revision. `cases`
is a nonempty array sorted by `id` in lexicographic order.

| Case field | Meaning |
| --- | --- |
| `id` | Unique case ID using ASCII letters, digits, hyphens, or underscores and starting with a letter or digit. IDs that differ only in capitalization collide. |
| `track` | Nonempty benchmark track name, such as `acoustic`. |
| `source_group` | Nonempty identifier shared by cases derived from the same source. |
| `provenance` | Nonempty description of where the case data came from or how it was generated. |
| `license` | Nonempty license identifier or reference, such as `MIT`. |
| `payloads` | Object with entries named `audio.ulaw`, `events.jsonl`, and `expected.json`. Each entry contains `path` and `sha256`. |

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
answerer, pair membership, verdict, and actions through `.expected`. Case
metadata lives on the enclosing case. Replay passes `.observable` to the
case runner.

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

## Canonical bytes

Use UTF-8 without a BOM and LF line endings, including a final LF. Sort object
keys lexicographically at every level. Use two-space indentation for JSON.
In JSONL, write one compact object per line without spaces between tokens or
blank lines. Preserve non-ASCII characters in UTF-8. Duplicate keys and
non-finite JSON numbers are invalid. `canonical_json` and `canonical_jsonl`
in `whoanswered.fixtures` emit these encodings. Arrange arrays and event
records in their specified order before encoding.
