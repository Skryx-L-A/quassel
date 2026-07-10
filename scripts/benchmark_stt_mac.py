#!/usr/bin/env python3
"""Benchmark STT engines (whisper.cpp + parakeet-mlx) on macOS.

Given a directory of NN.wav files with matching NN.txt reference transcripts,
runs each file through whisper-cli (per configured ggml model) and parakeet-mlx,
measuring wall time, real-time factor (RTF), word error rate (WER), and peak
memory (via /usr/bin/time -l, best-effort).

Usage:
    .venv/bin/python scripts/benchmark_stt_mac.py <samples_dir> [--lang de|en|auto]
"""

import argparse
import re
import shutil
import string
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

WHISPER_CLI = Path.home() / "AI/VoxType/vendor/whisper.cpp/build/bin/whisper-cli"
MODEL_DIR = Path.home() / "Library/Application Support/Quassel/models"

WHISPER_MODELS = {
    "whisper-large-v3-turbo-q5_0": MODEL_DIR / "ggml-large-v3-turbo-q5_0.bin",
    "whisper-small-q5_1": MODEL_DIR / "ggml-small-q5_1.bin",
}

PUNCT_TABLE = str.maketrans("", "", string.punctuation + "„“”‚‘’«»")


def normalize(text: str) -> list[str]:
    text = text.lower().translate(PUNCT_TABLE)
    text = re.sub(r"\s+", " ", text).strip()
    return text.split() if text else []


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over words, normalized by reference length."""
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0

    prev_row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur_row = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            cost = 0 if r == h else 1
            cur_row[j] = min(
                prev_row[j] + 1,       # deletion
                cur_row[j - 1] + 1,    # insertion
                prev_row[j - 1] + cost,  # substitution
            )
        prev_row = cur_row
    distance = prev_row[len(hyp)]
    return distance / len(ref)


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def run_timed(cmd: list[str], use_time: bool = True) -> tuple[float, str, int | None]:
    """Run cmd, return (wall_seconds, combined_output, peak_rss_kb_or_None)."""
    import time
    if use_time and shutil.which("/usr/bin/time"):
        # Wall time measured in Python: /usr/bin/time's "real" output is
        # locale-formatted (e.g. "0,49 real" under de_DE) and unsafe to parse.
        full_cmd = ["/usr/bin/time", "-l"] + cmd
        t0 = time.time()
        proc = subprocess.run(full_cmd, capture_output=True, text=True)
        wall = time.time() - t0
        out = proc.stdout + proc.stderr
        m = re.search(r"(\d+)\s+maximum resident set size", out)
        peak_kb = int(m.group(1)) // 1024 if m else None
        return wall, out, peak_kb
    else:
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        wall = time.time() - t0
        return wall, proc.stdout + proc.stderr, None


def transcribe_whisper(wav: Path, model_path: Path, lang: str, out_dir: Path) -> tuple[str, float, int | None]:
    out_base = out_dir / wav.stem
    cmd = [
        str(WHISPER_CLI), "-m", str(model_path), "-f", str(wav),
        "-l", lang, "-otxt", "-of", str(out_base), "-nt",
    ]
    wall, _, peak_kb = run_timed(cmd)
    txt_path = out_base.with_suffix(".txt")
    text = txt_path.read_text().strip() if txt_path.exists() else ""
    return text, wall, peak_kb


def transcribe_parakeet(wav: Path, out_dir: Path) -> tuple[str, float, int | None]:
    cmd = [
        "parakeet-mlx", "--output-format", "txt", "--output-dir", str(out_dir), str(wav),
    ]
    wall, _, peak_kb = run_timed(cmd)
    txt_path = out_dir / f"{wav.stem}.txt"
    text = txt_path.read_text().strip() if txt_path.exists() else ""
    return text, wall, peak_kb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("samples_dir", type=Path)
    parser.add_argument("--lang", default="de", help="language code for whisper (de/en/auto)")
    args = parser.parse_args()

    samples_dir = args.samples_dir
    wavs = sorted(samples_dir.glob("*.wav"))
    if not wavs:
        print(f"No .wav files found in {samples_dir}", file=sys.stderr)
        sys.exit(1)

    rows = []
    with tempfile.TemporaryDirectory(prefix="stt_bench_") as tmp:
        out_dir = Path(tmp)
        for wav in wavs:
            ref_path = wav.with_suffix(".txt")
            if not ref_path.exists():
                print(f"skip {wav.name}: no reference .txt", file=sys.stderr)
                continue
            reference = ref_path.read_text().strip()
            duration = wav_duration_seconds(wav)

            engines = list(WHISPER_MODELS.items()) + [("parakeet-mlx", None)]
            for engine_name, model_path in engines:
                if model_path is not None:
                    if not model_path.exists():
                        print(f"skip {engine_name}: model missing at {model_path}", file=sys.stderr)
                        continue
                    hyp, wall, peak_kb = transcribe_whisper(wav, model_path, args.lang, out_dir)
                else:
                    hyp, wall, peak_kb = transcribe_parakeet(wav, out_dir)

                wer = word_error_rate(reference, hyp)
                rtf = wall / duration if duration > 0 else float("nan")
                rows.append({
                    "file": wav.name,
                    "engine": engine_name,
                    "duration_s": duration,
                    "wall_s": wall,
                    "rtf": rtf,
                    "wer": wer,
                    "peak_mem_mb": (peak_kb / 1024) if peak_kb else None,
                    "hypothesis": hyp,
                })

    print_table(rows)


def print_table(rows: list[dict]) -> None:
    header = ["file", "engine", "duration_s", "wall_s", "rtf", "wer", "peak_mem_mb"]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(["---"] * len(header)) + "|")
    for r in rows:
        mem = f"{r['peak_mem_mb']:.0f}" if r["peak_mem_mb"] else "n/a"
        print(
            f"| {r['file']} | {r['engine']} | {r['duration_s']:.2f} | "
            f"{r['wall_s']:.2f} | {r['rtf']:.3f} | {r['wer']:.3f} | {mem} |"
        )


if __name__ == "__main__":
    main()
