"""Aufnahme unter Windows über sounddevice (PortAudio/WASAPI).

Schreibt rohes PCM (s16le, 16 kHz, mono) in dieselben RAW-Dateien wie auf
Linux — damit funktionieren Teiltranskripte (Live-Vorschau) und das
WAV-Verpacken aus quassel.audio unverändert.
"""
import os
import threading
import time

import sounddevice as sd

from ..audio import RAW_B, RATE, SAMPLE_BYTES
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
    """API-kompatibel zu quassel.audio.Recorder (start/stop/raw_bytes).

    Wie dort schreibt der Diktat-Pfad je Aufnahme abwechselnd in zwei
    Rohdateien (rec.raw, rec-b.raw): ein neues Diktat, das im Nachlauf des
    vorigen beginnt, kürzt nicht die Datei, die dessen finish() noch liest.
    Ein eigener raw_path (Wake-Listener) bleibt bei genau dieser einen Datei,
    damit Wake-Word und Diktat sich nie dieselbe Datei teilen."""

    stream_abandoned = False     # nur macOS kennt aufgegebene Streams

    def __init__(self, raw_path=None):
        raw_path = raw_path or RAW
        self._paths = [raw_path, RAW_B] if raw_path == RAW else [raw_path]
        self._slot = -1          # erster start() nimmt wieder die vorderste
        self.raw_path = raw_path  # gerade bzw. zuletzt beschriebene Datei
        self.last_path = raw_path  # von stop() zuletzt geschlossene Datei
        self.running_path = None  # Datei der LAUFENDEN Aufnahme (None = keine)
        self.stream = None
        self.outfile = None
        self.started = 0.0
        # Schützt nur den Besitzwechsel von Stream und Datei: der Not-Aus
        # kann aus einem anderen Thread in ein laufendes stop() fallen.
        self._lock = threading.Lock()

    @property
    def active(self):
        stream = self.stream
        return stream is not None and stream.active

    def start(self, mic="default"):
        os.makedirs(RUNDIR, exist_ok=True)
        # Ein Vorgänger, den niemand beendet hat, wird zuerst abgeräumt: er
        # hielte sonst das Mikrofon und schriebe still weiter.
        if self.stream is not None:
            self.stop()
        self._slot = (self._slot + 1) % len(self._paths)
        self.raw_path = self._paths[self._slot]
        outfile = open(self.raw_path, "wb")
        device = None
        if mic and mic != "default":
            try:
                device = int(mic)
            except ValueError:
                device = mic

        # Der Callback schreibt in SEINE Datei (lokale Referenz), nie in die
        # einer späteren Aufnahme.
        def callback(indata, _frames, _time, _status):
            if not outfile.closed:
                outfile.write(bytes(indata))

        try:
            stream = sd.RawInputStream(
                samplerate=RATE, channels=1, dtype="int16",
                device=device, callback=callback)
            stream.start()
        except Exception:  # noqa: BLE001 — z.B. kein Mikrofon
            outfile.close()
            return False
        with self._lock:
            self.outfile = outfile
            self.stream = stream
            self.running_path = self.raw_path
            self.started = time.monotonic()
        return True

    def raw_bytes(self, path=None):
        """Rohdaten lesen. Ohne path die laufende bzw. zuletzt beschriebene
        Aufnahme; mit path genau diese Datei — so bekommt ein wartendes
        finish() die Bytes SEINES Diktats."""
        path = path or self.raw_path
        try:
            outfile = self.outfile
            if outfile is not None and path == self.running_path:
                try:
                    outfile.flush()
                except ValueError:    # eben geschlossen
                    pass
            with open(path, "rb") as f:
                data = f.read()
            return data[:len(data) - (len(data) % SAMPLE_BYTES)]
        except OSError:
            return b""

    def stop(self):
        """Aufnahme beenden. Rückgabe (und self.last_path): die eben
        geschlossene Rohdatei, für raw_bytes(path)."""
        with self._lock:
            path = self.last_path = self.running_path or self.raw_path
            self.running_path = None
            stream, self.stream = self.stream, None
            outfile, self.outfile = self.outfile, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        if outfile is not None:
            outfile.close()
        return path
