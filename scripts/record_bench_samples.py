#!/usr/bin/env python3
"""Interactive recorder for STT benchmark samples.

Shows a reference sentence, records it via the microphone at 16kHz mono, and
saves NN.wav + NN.txt into bench_samples/. Run this yourself (human speaker) --
it is not meant to run unattended.

Usage:
    .venv/bin/python scripts/record_bench_samples.py [--out-dir bench_samples]
"""

import argparse
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000

SENTENCES = [
    ("de", "Können Sie mir bitte sagen, wie spät es ist? Es ist ungefähr halb drei."),
    ("de", "Die Rechnung beläuft sich auf 42 Euro und 17 Cent für drei Artikel."),
    ("de", "Wir haben heute Milch, Brot, Käse, Äpfel und, wenn möglich, auch Marmelade gekauft."),
    ("en", "The quick brown fox jumps over the lazy dog near the riverbank."),
    ("en", "Please schedule the meeting for next Tuesday at 3:30 in the afternoon."),
    ("en", "I would like to order two coffees and one croissant, thank you very much."),
    (
        "de",
        "Am Samstagmorgen bin ich früh aufgestanden und mit dem Fahrrad zum "
        "Wochenmarkt gefahren. Dort habe ich frisches Gemüse, zwei Kilo Äpfel und "
        "ein großes Roggenbrot gekauft. Der Händler erzählte mir, dass die Preise "
        "wegen des trockenen Sommers um fast fünfzehn Prozent gestiegen sind. "
        "Anschließend traf ich meine Schwester in einem kleinen Café am Marktplatz, "
        "wo wir über ihre geplante Reise nach Österreich sprachen. Sie will im "
        "Oktober für zehn Tage nach Wien und Salzburg, obwohl sie eigentlich lieber "
        "ans Meer fährt. Auf dem Rückweg begann es plötzlich zu regnen, und ich war "
        "völlig durchnässt, als ich endlich zu Hause ankam.",
    ),
]


def record_one(seconds_hint: int | None = None) -> np.ndarray:
    print("Recording... press Enter to stop.")
    chunks: list[np.ndarray] = []

    def callback(indata, frames, time_info, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback):
        input()

    if not chunks:
        return np.zeros((0,), dtype="int16")
    return np.concatenate(chunks, axis=0).flatten()


def save_wav(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio.tobytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("bench_samples"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Recording {len(SENTENCES)} samples into {args.out_dir}/")
    print("For each: read the sentence naturally, press Enter to start, speak, press Enter to stop.\n")

    for i, (lang, text) in enumerate(SENTENCES, start=1):
        idx = f"{i:02d}"
        print(f"--- Sample {idx} [{lang}] ---")
        print(text)
        input("Press Enter when ready to start recording...")
        audio = record_one()
        wav_path = args.out_dir / f"{idx}.wav"
        txt_path = args.out_dir / f"{idx}.txt"
        save_wav(wav_path, audio)
        txt_path.write_text(text.strip() + "\n")
        duration = len(audio) / SAMPLE_RATE
        print(f"Saved {wav_path.name} ({duration:.1f}s) and {txt_path.name}\n")

    print("Done. All samples saved to", args.out_dir)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
