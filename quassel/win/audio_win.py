"""Aufnahme unter Windows über sounddevice (PortAudio/WASAPI).

Schreibt rohes PCM (s16le, 16 kHz, mono) in dieselben RAW-Dateien wie auf
Linux — damit funktionieren Teiltranskripte (Live-Vorschau) und das
WAV-Verpacken aus quassel.audio unverändert.
"""
import os
import queue
import threading
import time
from collections import deque

import sounddevice as sd

from ..audio import RAW_B, RATE, SAMPLE_BYTES, _quiet_for
from ..state import RAW, RUNDIR

STOP_TIMEOUT = 1.0
# Obergrenze der Schreib-Warteschlange in Bytes: 60 s Audio (16 kHz, mono,
# 16 bit = 32 000 B/s, rund 1,9 MB). Ein kurzer Hänger der Platte verliert so
# keine Aufnahme; hängt sie dauerhaft, wächst der Speicher nicht weiter.
MAX_PENDING_BYTES = 60 * RATE * SAMPLE_BYTES


class _WriteQueue:
    """Warteschlange vom Audio-Callback zum Writer, begrenzt in Bytes.

    Audio geht nur über put_audio hinein und wird verworfen, wenn die Grenze
    überschritten wäre; das blockiert nie. Steuerzeichen (Ende, Barriere)
    gehen über put_control und zählen nicht gegen die Grenze."""

    def __init__(self, max_bytes):
        self._items = queue.Queue()
        self._max_bytes = max_bytes
        self._pending = 0          # Bytes in der Schlange plus im laufenden write
        self._lock = threading.Lock()

    def put_audio(self, chunk):
        with self._lock:
            if self._pending + len(chunk) > self._max_bytes:
                return False
            self._pending += len(chunk)
            self._items.put_nowait(chunk)
            return True

    def put_control(self, item):
        self._items.put_nowait(item)

    def get(self):
        return self._items.get()

    def done(self, chunk):
        """Der Writer hat den Block geschrieben: er belegt die Grenze nicht mehr."""
        with self._lock:
            self._pending -= len(chunk)


def list_mics():
    """[(stabiler Gerätename, Beschreibung)] der WASAPI-Eingabegeräte."""
    out = []
    try:
        hosts = sd.query_hostapis()
        seen = set()
        for i, dev in enumerate(sd.query_devices()):
            host = hosts[dev["hostapi"]]["name"]
            if (host == "Windows WASAPI" and dev.get("max_input_channels", 0) > 0
                    and dev["name"] not in seen):
                out.append((dev["name"], dev["name"]))
                seen.add(dev["name"])
    except Exception:  # noqa: BLE001 — Audio-Backend kaputt -> leere Liste
        pass
    return out


def resolve_mic(mic):
    """Resolve a saved name against the current device table at each start."""
    if not mic or mic == "default":
        try:
            index = sd.default.device[0]
            return index if index is not None and index >= 0 else None
        except (AttributeError, IndexError, TypeError):
            return None
    devices = sd.query_devices()
    if str(mic).isdigit():  # existing configurations saved an index
        index = int(mic)
        return index if index < len(devices) and devices[index].get("max_input_channels", 0) else None
    hosts = sd.query_hostapis()
    for index, dev in enumerate(devices):
        if (dev.get("max_input_channels", 0) > 0 and dev["name"] == mic
                and hosts[dev["hostapi"]]["name"] == "Windows WASAPI"):
            return index
    raise ValueError("configured microphone is unavailable")


class Recorder:
    """API-kompatibel zu quassel.audio.Recorder (start/stop/raw_bytes).

    Wie dort schreibt der Diktat-Pfad je Aufnahme abwechselnd in zwei
    Rohdateien (rec.raw, rec-b.raw): ein neues Diktat, das im Nachlauf des
    vorigen beginnt, kürzt nicht die Datei, die dessen finish() noch liest.
    Ein eigener raw_path (Wake-Listener) bleibt bei genau dieser einen Datei,
    damit Wake-Word und Diktat sich nie dieselbe Datei teilen."""

    def __init__(self, raw_path=None, preroll_ms=0, preroll_keep_s=3.0):
        raw_path = raw_path or RAW
        self._paths = [raw_path, RAW_B] if raw_path == RAW else [raw_path]
        self._slot = -1          # erster start() nimmt wieder die vorderste
        self.raw_path = raw_path  # gerade bzw. zuletzt beschriebene Datei
        self.last_path = raw_path  # von stop() zuletzt geschlossene Datei
        self.running_path = None  # Datei der LAUFENDEN Aufnahme (None = keine)
        self.stream = None
        self.outfile = None
        self.started = 0.0
        self.preroll_ms = max(0, min(2000, int(preroll_ms or 0)))
        self.preroll_keep_s = max(0.1, float(preroll_keep_s))
        self._max_ring_bytes = RATE * SAMPLE_BYTES * self.preroll_ms // 1000
        self._ring = deque()
        self._ring_bytes = 0
        self._device = None
        self._capture_token = None
        self._warm_timer = None
        self._warm_generation = 0
        self._write_queue = None
        self._writer_thread = None
        self.stream_abandoned = False
        # Riss die letzte Aufnahme ab? "ended"/"stalled"/"overflow"/None, gesetzt
        # von stop() ("ended"/"stalled": dieselbe Bedeutung wie
        # quassel.audio.Recorder.interrupted, D65; "overflow": die Schreib-
        # Warteschlange war voll, Audio ging verloren).
        self.interrupted = None
        self._dropped = False     # Block der laufenden Aufnahme verworfen?
        self._last_block = None   # monotonic des letzten Callback-Blocks
        # Schützt nur den Besitzwechsel von Stream und Datei: der Not-Aus
        # kann aus einem anderen Thread in ein laufendes stop() fallen.
        self._lock = threading.Lock()

    @property
    def active(self):
        stream = self.stream
        return self.running_path is not None and stream is not None and stream.active

    def _close_warm(self, generation=None):
        with self._lock:
            if generation is not None and generation != self._warm_generation:
                return
            if self.outfile is not None:
                return
            stream, self.stream = self.stream, None
            self._capture_token = None
            self._warm_timer = None
            self._ring.clear()
            self._ring_bytes = 0
        if stream is not None:
            def close_stream():
                try:
                    stream.stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    self.stream_abandoned = True
            closer = threading.Thread(target=close_stream, name="quassel-win-audio-close",
                                      daemon=True)
            closer.start()
            closer.join(timeout=STOP_TIMEOUT)
            if closer.is_alive():
                self.stream_abandoned = True

    def _start_writer(self, outfile):
        chunks = _WriteQueue(MAX_PENDING_BYTES)

        def write_chunks():
            try:
                while True:
                    item = chunks.get()
                    if item is None:
                        break
                    if isinstance(item, threading.Event):
                        outfile.flush()
                        item.set()
                    else:
                        outfile.write(item)
                        chunks.done(item)
            except OSError:
                self.stream_abandoned = True
            finally:
                outfile.close()

        writer = threading.Thread(target=write_chunks, name="quassel-win-audio-writer",
                                  daemon=True)
        self._write_queue = chunks
        self._writer_thread = writer
        writer.start()

    def start(self, mic="default"):
        if self.stream_abandoned:
            return False
        os.makedirs(RUNDIR, mode=0o700, exist_ok=True)
        # Ein Vorgänger, den niemand beendet hat, wird zuerst abgeräumt: er
        # hielte sonst das Mikrofon und schriebe still weiter.
        if self.running_path is not None:
            self.stop()
            if self.stream_abandoned:
                return False
        try:
            device = resolve_mic(mic)
        except Exception:  # noqa: BLE001 — verschwundenes oder defektes Gerät
            return False
        with self._lock:
            timer, self._warm_timer = self._warm_timer, None
            self._warm_generation += 1
        if timer is not None:
            timer.cancel()
        if self.stream is not None and (not self.preroll_ms or device != self._device
                                        or not self.stream.active):
            self._close_warm()
            if self.stream_abandoned:
                return False
        self._slot = (self._slot + 1) % len(self._paths)
        self.raw_path = self._paths[self._slot]
        try:
            outfile = open(self.raw_path, "wb")
        except OSError:
            return False

        if self.stream is not None:
            self._start_writer(outfile)
            with self._lock:
                self._dropped = False
                for chunk in self._ring:
                    if not self._write_queue.put_audio(chunk):
                        self._dropped = True
                self._ring.clear()
                self._ring_bytes = 0
                self.outfile = outfile
                self.running_path = self.raw_path
                self.started = time.monotonic()
                self.interrupted = None
            return True

        token = object()
        self._capture_token = token
        self._start_writer(outfile)
        # A retired stream cannot write into a later dictation.
        def callback(indata, _frames, _time, _status):
            chunk = bytes(indata)
            with self._lock:
                if token is not self._capture_token:
                    return
                self._last_block = time.monotonic()
                if self.outfile is not None:
                    if not self._write_queue.put_audio(chunk):
                        self._dropped = True
                elif self._max_ring_bytes:
                    self._ring.append(chunk)
                    self._ring_bytes += len(chunk)
                    while self._ring_bytes > self._max_ring_bytes:
                        first = self._ring.popleft()
                        excess = self._ring_bytes - self._max_ring_bytes
                        if len(first) > excess:
                            self._ring.appendleft(first[excess:])
                            self._ring_bytes -= excess
                        else:
                            self._ring_bytes -= len(first)

        try:
            with self._lock:
                self.outfile = outfile
                self.running_path = self.raw_path
                self._dropped = False
            stream = sd.RawInputStream(
                samplerate=RATE, channels=1, dtype="int16",
                device=device, callback=callback)
            stream.start()
        except Exception:  # noqa: BLE001 — z.B. kein Mikrofon
            if "stream" in locals():
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                self.outfile = None
                self.running_path = None
                self._capture_token = None
            self._write_queue.put_control(None)
            self._writer_thread.join(timeout=STOP_TIMEOUT)
            if self._writer_thread.is_alive():
                self.stream_abandoned = True
            return False
        with self._lock:
            self.stream = stream
            self._device = device
            self.started = time.monotonic()
            self.interrupted = None
        return True

    def capture_interrupted(self, now=None):
        """Riss die laufende Aufnahme ab? "ended": der Stream lief aus (Gerät
        weg, Treiber), "overflow": die Schreib-Warteschlange war voll (Platte
        hängt), "stalled": seit CAPTURE_STALL_S kam kein Block mehr.
        None: läuft, oder es läuft keine Aufnahme."""
        with self._lock:
            if self.running_path is None or self.stream is None:
                return None
            stream, last, started = self.stream, self._last_block, self.started
            dropped = self._dropped
        if not stream.active:
            return "ended"
        if dropped:
            return "overflow"
        if last is not None and last < started:
            last = None          # Block aus dem Vorlauf zählt nicht als Aufnahme
        return _quiet_for(last, started, time.monotonic() if now is None else now)

    def raw_bytes(self, path=None):
        """Rohdaten lesen. Ohne path die laufende bzw. zuletzt beschriebene
        Aufnahme; mit path genau diese Datei — so bekommt ein wartendes
        finish() die Bytes SEINES Diktats."""
        path = path or self.raw_path
        try:
            with self._lock:
                barrier = None
                if self.outfile is not None and path == self.running_path:
                    barrier = threading.Event()
                    self._write_queue.put_control(barrier)
            if barrier is not None:
                barrier.wait(timeout=STOP_TIMEOUT)
            with open(path, "rb") as f:
                data = f.read()
            return data[:len(data) - (len(data) % SAMPLE_BYTES)]
        except OSError:
            return b""

    def stop(self):
        """Aufnahme beenden. Rückgabe (und self.last_path): die eben
        geschlossene Rohdatei, für raw_bytes(path)."""
        # Vor dem Abräumen fragen: danach läuft kein Stream mehr, an dem sich
        # ein Abriss ablesen ließe.
        try:
            reason = self.capture_interrupted()
        except Exception:  # noqa: BLE001 — Prüfung darf das Beenden nie stören
            reason = None
        with self._lock:
            if self.running_path is not None:
                self.interrupted = reason
            path = self.last_path = self.running_path or self.raw_path
            self.running_path = None
            outfile, self.outfile = self.outfile, None
            chunks, self._write_queue = self._write_queue, None
            writer, self._writer_thread = self._writer_thread, None
            if chunks is not None:
                chunks.put_control(None)
            keep_warm = self.preroll_ms > 0 and self.stream is not None
        if writer is not None:
            writer.join(timeout=STOP_TIMEOUT)
            if writer.is_alive():
                self.stream_abandoned = True
        if keep_warm:
            with self._lock:
                self._warm_generation += 1
                timer = threading.Timer(self.preroll_keep_s, self._close_warm,
                                        args=(self._warm_generation,))
                timer.daemon = True
                self._warm_timer = timer
            timer.start()
        else:
            self._close_warm()
        return path
