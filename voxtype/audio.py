"""Audio-Aufnahme (PipeWire/Pulse) und WAV-Verpackung.

Aufgenommen wird rohes PCM (s16le, 16 kHz, mono) über stdout — daraus lassen
sich während der Aufnahme Teilstücke für die Live-Vorschau schneiden.
"""
import os
import shutil
import struct
import subprocess
import time
import wave

from .state import RAW, RUNDIR

RATE, SAMPLE_BYTES = 16000, 2


def record_command(mic="default"):
    if shutil.which("pw-record"):
        cmd = ["pw-record", "--rate", str(RATE), "--channels", "1",
               "--format", "s16"]
        if mic and mic != "default":
            cmd += ["--target", mic]
        return cmd + ["-"]
    if shutil.which("parecord"):
        cmd = ["parecord", "--raw", f"--rate={RATE}", "--channels=1",
               "--format=s16le"]
        if mic and mic != "default":
            cmd += ["-d", mic]
        return cmd
    return None


def wav_from_raw(raw_bytes, path):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_BYTES)
        w.setframerate(RATE)
        w.writeframes(raw_bytes)


def list_mics():
    """[(name, beschreibung)] der verfügbaren Aufnahmequellen (ohne Monitore)."""
    out = []
    try:
        r = subprocess.run(["pactl", "list", "sources"], capture_output=True,
                           text=True, timeout=5, check=False)
        name = desc = None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("Name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Description:"):
                desc = line.split(":", 1)[1].strip()
                if name and ".monitor" not in name:
                    out.append((name, desc))
                name = desc = None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def rms_level(window_s=0.15):
    """Pegel 0.0–1.0 aus dem Ende der laufenden Roh-Aufnahme (für die Pille)."""
    try:
        size = os.path.getsize(RAW)
        n = int(RATE * SAMPLE_BYTES * window_s)
        with open(RAW, "rb") as f:
            f.seek(max(0, size - n))
            data = f.read()
    except OSError:
        return 0.0
    data = data[:len(data) - (len(data) % SAMPLE_BYTES)]
    if len(data) < SAMPLE_BYTES * 32:
        return 0.0
    samples = struct.unpack(f"<{len(data)//2}h", data)
    acc = 0
    for s in samples:
        acc += s * s
    rms = (acc / len(samples)) ** 0.5
    return min(rms / 8000.0, 1.0)


class Recorder:
    def __init__(self):
        self.proc = None
        self.outfile = None
        self.started = 0.0

    @property
    def active(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, mic="default"):
        os.makedirs(RUNDIR, exist_ok=True)
        cmd = record_command(mic)
        if cmd is None:
            return False
        self.outfile = open(RAW, "wb")
        self.proc = subprocess.Popen(
            cmd, stdout=self.outfile, stderr=subprocess.DEVNULL)
        self.started = time.monotonic()
        return True

    def raw_bytes(self):
        try:
            with open(RAW, "rb") as f:
                data = f.read()
            return data[:len(data) - (len(data) % SAMPLE_BYTES)]
        except OSError:
            return b""

    def stop(self):
        if self.proc is None:
            return
        self.proc.send_signal(2)  # SIGINT
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
        if self.outfile:
            self.outfile.close()
            self.outfile = None
