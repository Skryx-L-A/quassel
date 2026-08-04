# Measurement, 4 August 2026: does the audio-window threshold hold on smaller models?

The 12-second threshold shipped in 2.6.0 rested on one model on one platform. Because
`whisperclient.py` is shared, Linux and Windows got the same shortened audio window — and
without an NVIDIA card those platforms load `small-q5_1` or `base-q5_1`, not the large model
that was measured. Whether a smaller model tolerates a shortened encoder window the same way
was an open question. This is the answer.

Measured on the Linux machine (Nobara, RTX 4070 SUPER) against the CUDA build of
`whisper-server` — the same binary the Quassel installation there uses. Same method as
`../2026-08-03-audio-ctx-und-beam-size/`: five requests per point, median wall time, word
error rate against the reference text, `temperature=0.0`, `audio_ctx` varied as a request
field only. 96 points in total, three models × sixteen lengths × two settings.

## The answer

The threshold holds on every model tested, with room to spare. None of them degrades before
14.3 seconds.

| Model | word error rate unchanged up to | first worse | first collapse |
|---|---|---|---|
| `base-q5_1` | 14.34 s | 16.08 s | 18.03 s (time ×8.3) |
| `small` (unquantized) | 16.08 s | 18.03 s | none up to 48.4 s |
| `large-v3-turbo` (fp16) | 14.34 s | 16.08 s | 18.03 s (time ×7) |

The smaller models are not more fragile — `small` is the most forgiving of the three. It never
runs into the repetition loop at all: past its reach it stumbles over a single word group and
then carries on with the rest of the sentence, where `base-q5_1` and `large-v3-turbo` start
repeating whole sentences and take seven to eight times longer than with the full window.

## Why the control run matters

`large-v3-turbo` was measured as a control against the Mac numbers, and it lands on the same
values: at 16.08 s both machines go from 0.061 to 0.091, and at 18.03 s both collapse into the
same repetition loop — visible in the transcript, where the sentence about the trader and the
prices appears twice. Different hardware, different quantization (fp16 here, q5_0 there),
identical behaviour. That is what makes the numbers for the two smaller models trustworthy:
the method reproduces.

## Still not measured

Windows. It runs the same shared code and the same model sizes, so the result carries over on
paper, but nobody has run the sweep there.

## The files

`ac_sweep_base-q5_1.json`, `ac_sweep_small.json`, `ac_sweep_large-v3-turbo.json` — every point
with its five timings, the median, the spread, the hypothesis text and the word error rate.
Absolute paths are replaced with `<repo>`, `<home>`, `<modelle>` and `<whispercpp>`.

`small-q5_1` was not available on the machine; the unquantized `ggml-small.bin` stands in for
its size class. That shifts the absolute error rate of that column but not the effect being
measured, which is the difference between the two audio-window settings on the same model.
