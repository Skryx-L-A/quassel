#!/usr/bin/env python3
"""Baut Testmaterial: (a) echte, wortgrenzengenaue Ausschnitte aus
bench_samples/07.wav (kein Verketten), (b) synthetisch erschwerte Kopien
(Rauschen / leise) aller echten bench_samples-Dateien fuer die bs1-vs-bs5-Frage.
Aendert nichts im Repo, schreibt nur hierher."""
import json
import os
import subprocess
import wave

REPO = "<repo>"
HERE = os.path.dirname(os.path.abspath(__file__))
SEG_DIR = os.path.join(HERE, "segments")
NOISY_DIR = os.path.join(HERE, "synth_noisy")
QUIET_DIR = os.path.join(HERE, "synth_quiet")

# Wortgrenzen aus eigener -ml 1 Ausrichtung von 07.wav (siehe HOW-verified).
# (wort_index, schnitt_sekunde)
CUTS = [
    (22, 9.91), (26, 12.62), (29, 14.34), (33, 16.08),
    (36, 18.03), (40, 20.33), (43, 21.84), (46, 23.98), (51, 26.22),
]


def build_segments():
    src = os.path.join(REPO, "bench_samples", "07.wav")
    ref_words = open(os.path.join(REPO, "bench_samples", "07.txt"), encoding="utf-8").read().split()
    with wave.open(src, "rb") as w:
        params = w.getparams()
        rate = params.framerate
        sampwidth = params.sampwidth
        frames = w.readframes(w.getnframes())
    items = []
    for wi, cut_s in CUTS:
        n_samples = int(cut_s * rate)
        n_bytes = n_samples * sampwidth
        clip = frames[:n_bytes]
        actual_s = len(clip) / (rate * sampwidth)
        out = os.path.join(SEG_DIR, "seg_%02d_%05.2fs.wav" % (wi, actual_s))
        with wave.open(out, "wb") as ow:
            ow.setnchannels(params.nchannels)
            ow.setsampwidth(sampwidth)
            ow.setframerate(rate)
            ow.writeframes(clip)
        ref = " ".join(ref_words[:wi])
        items.append({"path": out, "ref": ref, "duration_s": actual_s, "word_idx": wi,
                      "source": "bench_samples/07.wav wortgrenzengenau geschnitten (real, nicht verkettet)"})
    with open(os.path.join(HERE, "segments_manifest.json"), "w") as f:
        json.dump(items, f, indent=1, ensure_ascii=False)
    return items


def build_synth(real_files):
    """real_files: list of (wav_path, txt_path). Erzeugt je Datei eine
    verrauschte (SNR ~15dB weisses Rauschen) und eine leise (-18dB) Kopie."""
    items = []
    for wav, txt in real_files:
        stem = os.path.splitext(os.path.basename(wav))[0]
        srcdir = os.path.basename(os.path.dirname(wav))
        noisy = os.path.join(NOISY_DIR, "%s_%s.wav" % (srcdir, stem))
        quiet = os.path.join(QUIET_DIR, "%s_%s.wav" % (srcdir, stem))
        # Rauschen: weisses Rauschen bei -25dBFS auf das Originalsignal addiert
        # (amix, duration=first) -> hoerbar erschwertes, aber nicht zerstoertes Signal.
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", wav,
            "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.05:duration=999",
            "-filter_complex", "[1:a]atrim=0:99[n];[0:a][n]amix=inputs=2:duration=first:weights=1 0.35[aout]",
            "-map", "[aout]", "-ar", "16000", "-ac", "1", noisy,
        ], check=True)
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", wav, "-af", "volume=-18dB",
            "-ar", "16000", "-ac", "1", quiet,
        ], check=True)
        ref = open(txt, encoding="utf-8").read().strip()
        with wave.open(noisy) as w:
            dur = w.getnframes() / w.getframerate()
        items.append({"path": noisy, "ref": ref, "duration_s": dur,
                      "source": "synthetisch: %s + weisses Rauschen amplitude=0.05, gemischt weights=1:0.35 (ffmpeg anoisesrc+amix)" % wav})
        with wave.open(quiet) as w:
            dur = w.getnframes() / w.getframerate()
        items.append({"path": quiet, "ref": ref, "duration_s": dur,
                      "source": "synthetisch: %s um -18dB abgesenkt (ffmpeg volume=-18dB)" % wav})
    with open(os.path.join(HERE, "synth_manifest.json"), "w") as f:
        json.dump(items, f, indent=1, ensure_ascii=False)
    return items


if __name__ == "__main__":
    os.makedirs(SEG_DIR, exist_ok=True)
    os.makedirs(NOISY_DIR, exist_ok=True)
    os.makedirs(QUIET_DIR, exist_ok=True)
    segs = build_segments()
    print("segments:", len(segs))
    for s in segs:
        print(" ", s["path"], s["duration_s"], "words=%d" % s["word_idx"])

    real = []
    for d in ("bench_samples", "bench_samples_synth"):
        dd = os.path.join(REPO, d)
        for fn in sorted(os.listdir(dd)):
            if fn.endswith(".wav"):
                real.append((os.path.join(dd, fn), os.path.join(dd, fn[:-4] + ".txt")))
    synth = build_synth(real)
    print("synth:", len(synth))
