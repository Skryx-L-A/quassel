"""Aufnahme unter Windows über sounddevice (PortAudio/WASAPI).

Schreibt rohes PCM (s16le, 16 kHz, mono) in dieselbe RAW-Datei wie auf
Linux — damit funktionieren Teiltranskripte (Live-Vorschau) und das
WAV-Verpacken aus voxtype.audio unverändert.
"""
import os
import time

import sounddevice as sd

from ..audio import RATE, SAMPLE_BYTES
from ..state import RAW, RUNDIR


def list_mics():
    """[(geräte-index-str, beschreibung)] der Eingabegeräte."""
    out = []
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                out.append((str(i), dev["name"]))
    except Exception:  # noqa: BLE001 — Audio-Backend kaputt -> leere Liste
        pass
    return out


class Recorder:
    """API-kompatibel zu voxtype.audio.Recorder (start/stop/raw_bytes)."""

    def __init__(self):
        self.stream = None
        self.outfile = None
        self.started = 0.0

    @property
    def active(self):
        return self.stream is not None and self.stream.active

    def start(self, mic="default"):
        os.makedirs(RUNDIR, exist_ok=True)
        self.outfile = open(RAW, "wb")
        device = None
        if mic and mic != "default":
            try:
                device = int(mic)
            except ValueError:
                device = mic

        def callback(indata, _frames, _time, _status):
            if self.outfile and not self.outfile.closed:
                self.outfile.write(bytes(indata))

        try:
            self.stream = sd.RawInputStream(
                samplerate=RATE, channels=1, dtype="int16",
                device=device, callback=callback)
            self.stream.start()
        except Exception:  # noqa: BLE001 — z.B. kein Mikrofon
            self.outfile.close()
            self.outfile = None
            self.stream = None
            return False
        self.started = time.monotonic()
        return True

    def raw_bytes(self):
        try:
            if self.outfile:
                self.outfile.flush()
            with open(RAW, "rb") as f:
                data = f.read()
            return data[:len(data) - (len(data) % SAMPLE_BYTES)]
        except OSError:
            return b""

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:  # noqa: BLE001
                pass
            self.stream = None
        if self.outfile:
            self.outfile.close()
            self.outfile = None
