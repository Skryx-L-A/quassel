# Measurement, 3 August 2026: audio context and beam size

Two defaults in Quassel were set by assumption rather than by measurement. This is the data
that replaced the assumptions, kept here so the next person to touch either value can see what
it rests on instead of taking the source comments on trust.

Everything was measured on an Apple Silicon Mac (M5 Pro, 48 GB) against `whisper-server` from
the vendored whisper.cpp with the Metal backend, using `ggml-large-v3-turbo-q5_0` and the
Silero VAD model. No other model and no other platform was measured — the paragraph on limits
at the end says what follows from that.

## What was asked

**Does beam search earn its time?** Quassel started the speech server with `-bs 5` wherever it
found a GPU, on the assumption that the accuracy is worth the extra time there.

**How short does a recording have to be for a smaller audio context to be safe?** Whisper's
encoder always processes a 30-second window. `audio_ctx` shortens it, which is cheaper, but a
window shorter than the audio itself has to lose something.

## What came out

Beam search never won. Across 36 files and 938 reference words, `beam_size=5` produced a lower
word error rate in not a single file. In one noisy file it was worse: greedy search got "the
quick brown fox jumps over the lazy dog near the riverbank" right, beam search heard "dock" for
"dog". The median time was 0.595 s against 0.557 s, so beam search also cost about 6 %. The
default is now `-bs 1` everywhere.

The audio context is safe up to about 12.6 seconds and then stops being safe quickly:

| Length | full window | `audio_ctx=768` | `audio_ctx=1000` |
|---|---|---|---|
| 5.80 s | 0.484 s / WER 0.000 | 0.270 s / 0.000 | 0.339 s / 0.071 |
| 9.91 s | 0.533 s / 0.045 | 0.308 s / 0.045 | 0.391 s / 0.091 |
| 12.62 s | 0.560 s / 0.000 | 0.349 s / 0.000 | 0.423 s / 0.038 |
| 14.34 s | 0.581 s / 0.034 | 0.377 s / 0.069 | 0.453 s / 0.069 |
| 16.08 s | 0.609 s / 0.061 | 0.428 s / 0.091 | 0.491 s / 0.121 |
| 18.03 s | 0.619 s / 0.028 | 2.368 s / 0.667 | 0.520 s / 0.056 |
| 20.33 s | 0.637 s / 0.050 | 2.229 s / 0.325 | 0.539 s / 0.100 |
| 21.84 s | 0.661 s / 0.047 | 2.061 s / 2.023 | 0.888 s / 1.884 |

Past its reach the decoder does not simply get less accurate — it falls into a repetition loop
and takes three to four times **longer** than with the full window. That is the reason the
threshold in `quassel/whisperclient.py` sits at 12.0 s and not at the arithmetic limit of
768 / 1500 × 30 s = 15.36 s.

`audio_ctx=1000` reaches further, to about 20 s, and was rejected: it raised the word error
rate at every single length measured, mostly to double, to save around 0.1 s. `audio_ctx=1200`
was slower than the full window at every length and is of no use on this machine.

## The files

- `ac_sweep_result.json` — eleven lengths × four settings × five runs, with the median, the
  spread, the hypothesis text and the system load for each point.
- `bs_compare_result.json` — 36 files × two settings, with every hypothesis text.
- `segments_manifest.json` — the nine real cuts from `bench_samples/07.wav`, cut on word
  boundaries taken from a word-level transcription of the full 48-second recording, so each
  reference text is a genuine prefix of the original rather than a guess.
- `synth_manifest.json` — the artificially degraded copies and the exact `ffmpeg` filter used
  for each.
- `run_ac_sweep.py`, `run_bs_compare.py`, `prep_segments.py`, `common.py` — the scripts.

Absolute paths have been replaced with `<repo>`, `<home>`, `<modelle>` and `<messdaten>`; the
scripts need those filled in to run again.

## What this does not cover

One model on one platform. A Linux machine without an NVIDIA card runs `small-q5_1` or
`base-q5_1`, and a shorter encoder window does not have to behave the same way on a smaller
model. The threshold applies on every platform because the code is shared, but the evidence
behind it does not. Anyone raising it should measure on the target model first.
