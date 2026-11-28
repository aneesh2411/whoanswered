# v3 transcript and tone signals

The v3 transcripts and audio are authored synthetic signals. Real ASR output
and real phone audio are untested in the published results. One exploratory
real call is described under the optional late deadline. The detectors run entirely in the Python
standard library without network access.

## Transcript cues

`TranscriptCueDetector` uses the first finalized update with an unambiguous
cue. Interim text and empty updates carry no evidence. Matching is
case-insensitive for phrase lists, uses whole words, and tolerates punctuation.
The [cue file](../whoanswered/transcript_cues.json) lists mailbox and screener
phrases and human greeting patterns. Text with both mailbox and screener
phrases is ambiguous. A human cue is a greeting with a short question or a
name introduction, or a greeting directly followed by a capitalized name.
A bare “Hello?” carries no evidence. A wrong finalized update can still
trigger a cue.

## Tone detection

`ToneDetector` checks each 20 ms mono 8 kHz G.711 mu-law frame. It decodes to PCM,
removes the mean, and uses a Goertzel bank spaced 25 Hz apart to identify the
strongest frequency. A qualifying frame has a peak in the 400–2,000 Hz band
and at least 80% of its energy in that peak. The peak must stay within one
bank step of the run's starting frequency for 8–30 consecutive frames
(160–600 ms at this resolution). There must have been at least 40 ms of
consecutive active non-tone audio as a proxy for speech. A contiguous
non-tone frame must follow the run to verify its end. A tone by itself, a
missing end frame, or a tone that ends after cutoff cannot supply a mailbox
cue by the deadline. Replay uses media sample offsets for continuity and
arrival times for decisions.

## Fusion rule

`FusionDetector()` starts independent sessions for `V1Detector`,
`TranscriptCueDetector`, and `ToneDetector`. The constructor also accepts
these three components as `audio`, `transcript`, and `tone` detectors.

- A completed beep after speech is decisive for voicemail and vetoes
  pending human agreement. A screener phrase is decisive for a screener.
  If both decisive cues are present at the same update, fusion abstains.
- A mailbox phrase and v1 voicemail evidence commit on agreement.
- A human phrase and v1 human evidence commit at cutoff, after all inputs
  arriving at the deadline. Other single or conflicting cues yield
  `uncertain` at cutoff.
- The first commitment is final. A confirmed disconnect closes an
  uncommitted session. Cutoff closes a fusion session even if it abstains.

## Optional late deadline

`FusionDetector(late_deadline=True)` keeps a session open after it abstains at
`cutoff`. A completed beep after speech, a decisive screener phrase, or a
mailbox phrase that agrees with v1 can then still commit. Send `late_cutoff`
at your later deadline, after the cutoff, to apply the cutoff rule once more
and close the session. A repeated `cutoff` is ignored while it waits, and
`call_completed` also closes it. A committed decision never changes, and the default (`False`) keeps
the behavior described above, including the frozen reserved comparison.

One exploratory real call to a phone with a custom voicemail greeting (a
single sample, speech-to-text from a local model) had a greeting of about
4.5 s and a beep near 7 s. The 2,000 ms cutoff abstained, and the tone rule
alone committed voicemail at 7.1 s. Greetings that end after the cutoff may
need this option. All published results still use authored synthetic inputs.

## Development and reserved evaluation

Both [20-case splits](../fixtures/v3/README.md) were generated and pinned
before tuning. On the 15 eligible development cases, early human agreement
misclassified a mailbox before its beep arrived. Lowering the minimum to
100 ms misclassified a brief tone on a human call. Each alternative got
7 correct out of 8 commitments. Holding human agreement until the two-second
cutoff and using a 160 ms minimum got 8 correct out of 8 commitments, with
7 abstentions. The detector and these settings were then fixed for the
reserved comparison.

The reserved report scores the 15 unpaired human and mailbox cases at
2,000 ms, checks both matched pairs, records the screener verdict separately,
and checks scripted playback on the public v1 policy cases. It uses the
[v2 promotion rule](fixture-format.md#revision-2-evaluation-rules).
See the [published report](v3-eval-comparison.json).
