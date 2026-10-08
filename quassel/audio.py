"""Audio-Aufnahme (PipeWire/Pulse/CoreAudio) und WAV-Verpackung.

Aufgenommen wird rohes PCM (s16le, 16 kHz, mono) in eine Rohdatei — daraus
lassen sich während der Aufnahme Teilstücke für die Live-Vorschau schneiden.

Linux und Windows füllen die Datei aus einem Aufnahme-Subprozess
(pw-record/parecord), macOS in-process über einen sounddevice-Stream — die
Bytes-Schnittstelle (raw_bytes()) ist in beiden Fällen dieselbe.
"""
import json
from collections import deque
import math
import os
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave

from .state import RAW, RUNDIR

RATE, SAMPLE_BYTES = 16000, 2

# Zweite Rohdatei für den Diktat-Pfad. Die Aufnahmen wechseln sich zwischen
# beiden ab, damit ein neues Diktat die Datei des vorigen nicht überschreibt,
# solange dessen finish() sie noch ausliest.
RAW_B = os.path.join(os.path.dirname(RAW), "rec-b.raw")

# macOS-Aufnahmebackend. ffmpeg ist auf dem Mac nicht mitgebündelt (gegen 35
# dylibs gelinkt), deshalb nimmt die App über sounddevice/CoreAudio auf.
#   "sd16"      Stream direkt auf 16 kHz öffnen — CoreAudio konvertiert
#   "sdnative"  Stream auf der Geräterate + eigenes Polyphase-Resampling
#   "ffmpeg"    alter Subprozess-Pfad (nur mit ffmpeg im PATH)
# Umschaltbar zur Laufzeit über QUASSEL_MAC_AUDIO (siehe mac_backend()).
MAC_BACKEND_DEFAULT = "sd16"
MAC_BACKENDS = ("sd16", "sdnative", "ffmpeg")

# Harte Frist beim Beenden des macOS-Aufnahme-Streams. Auf einem defekten
# CoreAudio-Gerät kehrt PortAudio nie zurück (es wartet auf ein Callback-
# Signal, das nicht mehr kommt) — danach wird der Stream aufgegeben.
MAC_STOP_TIMEOUT = 2.0


def _log(msg):
    print("%s %s" % (time.strftime("%H:%M:%S"), msg), file=sys.stderr, flush=True)


def mac_backend():
    """Aktives macOS-Backend; unbekannte Werte fallen auf den Default zurück."""
    name = os.environ.get("QUASSEL_MAC_AUDIO", MAC_BACKEND_DEFAULT)
    return name if name in MAC_BACKENDS else MAC_BACKEND_DEFAULT


def record_command(mic="default"):
    if sys.platform == "darwin":
        # macOS: ffmpeg/AVFoundation liefert rohes PCM auf stdout (wie
        # pw-record). mic: Gerätename oder -index aus list_mics(), sonst
        # das Standard-Eingabegerät.
        if not shutil.which("ffmpeg"):
            return None
        target = mic if mic and mic != "default" else "default"
        return ["ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "avfoundation", "-i", f":{target}",
                "-ar", str(RATE), "-ac", "1", "-f", "s16le", "-"]
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
    if sys.platform == "darwin":
        return _list_mics_mac()
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


def newest_raw():
    """Zuletzt beschriebene der beiden Diktat-Rohdateien. Wer den Pegel liest,
    läuft in einem anderen Prozess als der Recorder und kann ihn nicht fragen,
    welche Datei gerade dran ist — die Änderungszeit sagt es."""
    best, newest = RAW, -1.0
    for path in (RAW, RAW_B):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > newest:
            best, newest = path, mtime
    return best


# Wächst die Rohdatei so lange nicht mehr, liefert die Aufnahme nichts (Gerät
# verklemmt, Stream tot): rms_level() meldet dann 0 statt des letzten Werts,
# damit die Pille die Stille zeigt und nicht einen eingefrorenen Ausschlag.
LEVEL_STALE_S = 0.5


def rms_level(window_s=0.15, now=None):
    """Pegel 0.0–1.0 aus dem Ende der laufenden Roh-Aufnahme (für die Pille)."""
    raw = newest_raw()
    try:
        now = time.time() if now is None else now
        if now - os.path.getmtime(raw) > LEVEL_STALE_S:
            return 0.0
        size = os.path.getsize(raw)
        n = int(RATE * SAMPLE_BYTES * window_s)
        with open(raw, "rb") as f:
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


class LevelPump:
    """Misst während einer Aufnahme den Pegel (rms_level) und reicht ihn
    LEVEL_HZ-mal je Sekunde an publish weiter — im Daemon an
    state.level_set, unter Windows an die Pille im selben Prozess.

    Läuft als eigener Thread, damit die Messung weder die Hotkey-Schleife
    noch die Live-Vorschau aufhält. Ein Fehler beim Messen oder Melden
    beendet nur die Anzeige, nie das Diktat."""

    def __init__(self, publish, hz=15, level=None):
        self.publish = publish
        self.interval = 1.0 / hz
        self.level = level or rms_level
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="level-pump",
                                        daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.publish(self.level())
            except Exception as e:  # noqa: BLE001 — Anzeige darf nie stören
                _log("audio: Pegelanzeige aus (%s)" % e)
                return

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


def _sd():
    """sounddevice-Modul oder None (fehlt z.B. in Test-Umgebungen)."""
    try:
        import sounddevice
    except Exception:      # noqa: BLE001 — ohne PortAudio-Lib nur ImportError-ähnlich
        return None
    return sounddevice


def _list_mics_mac():
    """Eingabegeräte auf macOS: [(name, name)] — über sounddevice/CoreAudio,
    beim ffmpeg-Backend (oder ohne sounddevice) über AVFoundation."""
    sd = None if mac_backend() == "ffmpeg" else _sd()
    if sd is None:
        return _list_mics_mac_ffmpeg()
    try:
        return [(d["name"], d["name"]) for d in sd.query_devices()
                if d.get("max_input_channels", 0) > 0]
    except Exception:      # noqa: BLE001 — Audio-Backend kaputt -> leere Liste
        return []


def _mac_device(mic):
    """Geräte-Index für mic (Name aus list_mics oder Index-String);
    None = Standard-Eingabegerät."""
    if not mic or mic == "default":
        from . import coreaudio
        device = coreaudio.default_input_device()
        mic = coreaudio.device_name(device) if device is not None else None
        if not mic:
            return None
    try:
        return int(mic)
    except ValueError:
        pass
    sd = _sd()
    if sd is None:
        return None
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0 and dev["name"] == mic:
                return i
    except Exception:      # noqa: BLE001 — Audio-Backend kaputt -> Standardgerät
        pass
    return None


def _mac_device_rate(device):
    """Native Abtastrate des Geräts (Fallback: 48 kHz)."""
    sd = _sd()
    try:
        info = sd.query_devices(sd.default.device[0] if device is None else device)
        rate = int(round(info["default_samplerate"]))
        return rate if rate > 0 else 48000
    except Exception:      # noqa: BLE001
        return 48000


def _list_mics_mac_ffmpeg():
    """AVFoundation-Audiogeräte über ffmpeg auflisten: [(name, name)]."""
    out = []
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "avfoundation",
             "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return out
    in_audio = False
    for line in r.stderr.splitlines():
        if "AVFoundation audio devices" in line:
            in_audio = True
            continue
        if "AVFoundation video devices" in line:
            in_audio = False
            continue
        if in_audio and "] [" in line:
            name = line.rsplit("]", 1)[1].strip()
            if name:
                out.append((name, name))
    return out


def _bluez_profiles():
    """{karte: aktives_profil} aller Bluetooth-Karten (für Profil-Restore)."""
    try:
        r = subprocess.run(["pactl", "--format=json", "list", "cards"],
                           capture_output=True, text=True, timeout=5, check=False)
        cards = json.loads(r.stdout)
        return {c["name"]: c.get("active_profile", "")
                for c in cards if c.get("name", "").startswith("bluez_card")}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _restore_bluez_profiles(before):
    """Bluetooth-Headsets schalten bei Mikrofonnutzung von A2DP (Musik) auf
    HFP (Headset) um und bleiben danach manchmal hängen -> kein Ton mehr,
    bis man neu verbindet. Deshalb: Profil nach der Aufnahme aktiv
    zurückschalten, falls es sich geändert hat."""
    def work():
        time.sleep(1.5)   # PipeWire erst selbst zurückschalten lassen
        after = _bluez_profiles()
        for card, profile in before.items():
            if profile and after.get(card) and after[card] != profile:
                subprocess.run(["pactl", "set-card-profile", card, profile],
                               check=False)
    threading.Thread(target=work, daemon=True).start()


class _Polyphase:
    """Streaming-Resampler (windowed-sinc, polyphase) für int16-Mono.

    Hält den Filterzustand über Blockgrenzen hinweg, damit beim
    blockweisen Füttern aus dem Audio-Callback keine Knackser entstehen.
    numpy wird nur hier gebraucht und deshalb erst zur Laufzeit importiert.
    """

    HALF = 24          # Filterlänge = 2*HALF*max(up,down)+1
    # Grenzfrequenz als Anteil der Nyquistfrequenz der kleineren Rate. Bewusst
    # UNTER 1: läge sie genau auf Nyquist, fiele der halbe Übergangsbereich
    # darüber und Material knapp über 8 kHz spiegelte sich hörbar ins Band
    # zurück (gemessen: -8 dB bei 8,4 kHz statt Sperrdämpfung).
    CUTOFF = 0.92

    def __init__(self, src_rate, dst_rate):
        import numpy as np
        self.np = np
        g = math.gcd(src_rate, dst_rate)
        self.up, self.down = dst_rate // g, src_rate // g
        m = max(self.up, self.down)
        n = 2 * self.HALF * m + 1
        t = np.arange(n) - (n - 1) / 2.0
        # Kaiser-Fenster hält die Sperrdämpfung hoch (gemessen < -60 dB).
        h = np.sinc(self.CUTOFF * t / m) * np.kaiser(n, 9.0)
        h = h / h.sum() * self.up
        pad = (-n) % self.up
        # phases[p][j] = h[j*up + p] — Phase p filtert die Eingangsvorgeschichte
        self.phases = np.pad(h, (0, pad)).reshape(-1, self.up).T.copy()
        self.taps = self.phases.shape[1]
        self.buf = np.zeros(self.taps - 1, dtype=np.float64)
        self.base = -(self.taps - 1)   # globaler Eingangsindex von buf[0]
        self.n = 0                     # nächster Ausgangsindex

    def feed(self, data):
        """int16-Bytes der Quellrate rein, int16-Bytes der Zielrate raus."""
        np = self.np
        x = np.frombuffer(data, dtype="<i2").astype(np.float64)
        if x.size:
            self.buf = np.concatenate((self.buf, x))
        last = self.base + self.buf.size - 1
        # größtes n mit (n*down)//up <= last
        n_end = ((last + 1) * self.up - 1) // self.down
        if n_end < self.n:
            return b""
        ns = np.arange(self.n, n_end + 1)
        q = ns * self.down
        idx = (q // self.up - self.base)[:, None] - np.arange(self.taps)[None, :]
        y = np.einsum("ij,ij->i", self.buf[idx], self.phases[q % self.up])
        self.n = n_end + 1
        drop = min((self.n * self.down) // self.up - self.taps + 1 - self.base,
                   self.buf.size)
        if drop > 0:
            self.buf = self.buf[drop:]
            self.base += drop
        return np.clip(np.rint(y), -32768, 32767).astype("<i2").tobytes()


def _close_stream_quiet(stream):
    """Aufnahme-Stream beenden: abort() (Pa_AbortStream) statt stop(), denn
    stop() wartet auf die Drainage der Callbacks — bei einem reinen
    EINGABE-Stream gibt es nichts zu leeren, wohl aber ein Gerät, das nie
    antwortet. Ältere sounddevice-Versionen ohne abort() nutzen stop()."""
    try:
        halt = getattr(stream, "abort", None) or stream.stop
        halt()
        stream.close()
    except Exception:  # noqa: BLE001 — Stream schon tot
        pass


class _MacStream:
    """macOS-Aufnahme über sounddevice/CoreAudio in dieselbe Rohdatei, die
    sonst der Aufnahme-Subprozess füllt.

    Der Audio-Callback legt die Blöcke nur in eine Queue; ein Writer-Thread
    resampelt (falls nötig) und schreibt sie — so kann kein Plattenzugriff den
    Callback ausbremsen und Dropouts verursachen.
    """

    def __init__(self, raw_path, native=False):
        self.raw_path = raw_path
        self.native = native       # nativ aufnehmen + selbst auf 16 kHz resampeln
        self.stream = None
        self.outfile = None
        self.thread = None
        self.queue = queue.Queue()
        self.started = 0.0
        self.in_rate = RATE
        self.overflows = 0         # von PortAudio gemeldete Input-Overflows
        self.first_block = None    # monotonic-Zeit des ersten Audioblocks
        self._abandoned = False    # Stream aufgegeben -> Callback liefert nichts mehr

    @property
    def active(self):
        return self.stream is not None

    def start(self, mic="default"):
        sd = _sd()
        if sd is None:
            return False
        device = _mac_device(mic)
        self.in_rate = _mac_device_rate(device) if self.native else RATE
        try:
            resampler = (_Polyphase(self.in_rate, RATE)
                         if self.in_rate != RATE else None)
        except ImportError:   # numpy fehlt -> "sdnative" ist nicht benutzbar
            return False
        try:
            self.outfile = open(self.raw_path, "wb")
        except OSError:
            return False

        def callback(indata, _frames, _time, status):
            # Aufgegebener Stream: sein Callback kann weiterlaufen, obwohl der
            # Writer-Thread längst beendet ist — was er einreiht, holt niemand
            # mehr ab, die Queue würde also unbegrenzt wachsen.
            if self._abandoned:
                return
            if status and getattr(status, "input_overflow", False):
                self.overflows += 1
            if self.first_block is None:
                self.first_block = time.monotonic()
            self.queue.put(bytes(indata))

        try:
            self.stream = sd.RawInputStream(
                samplerate=self.in_rate, channels=1, dtype="int16",
                device=device, callback=callback)
            self.stream.start()
        except Exception:  # noqa: BLE001 — kein Mikrofon / keine Berechtigung
            self.outfile.close()
            self.outfile = None
            self.stream = None
            return False
        self.thread = threading.Thread(target=self._writer, args=(resampler,),
                                       daemon=True)
        self.thread.start()
        self.started = time.monotonic()
        return True

    def _writer(self, resampler):
        while True:
            block = self.queue.get()
            if block is None:
                break
            if resampler is not None:
                block = resampler.feed(block)
            if block and self.outfile is not None:
                self.outfile.write(block)
                self.outfile.flush()

    def _halt_stream(self, stream):
        """Stream anhalten und schließen, aber mit harter Frist: kehrt
        CoreAudio nicht zurück, wird der Stream aufgegeben (Referenz fallen
        lassen, nie wieder anfassen) statt den Aufrufer zu blockieren. Die
        Rohdatei liegt vollständig auf der Platte — die Aufnahme ist also
        nicht verloren, nur der Stream bleibt liegen."""
        t = threading.Thread(target=_close_stream_quiet, args=(stream,),
                             daemon=True, name="mac-stream-stop")
        t.start()
        t.join(timeout=MAC_STOP_TIMEOUT)
        if t.is_alive():
            # Der Callback darf ab jetzt nichts mehr einreihen; was schon in der
            # Queue liegt, schreibt der Writer-Thread noch zu Ende.
            self._abandoned = True
            _log("audio: CoreAudio-Stream reagiert nicht (>%.1fs) -> aufgegeben"
                 % MAC_STOP_TIMEOUT)

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            self._halt_stream(stream)
        if self.thread is not None:
            self.queue.put(None)
            self.thread.join(timeout=5)
            self.thread = None
        if self.outfile is not None:
            self.outfile.close()
            self.outfile = None


class _PrerollCapture:
    """Opt-in continuously running input with a bounded PCM ring.

    The callback only enqueues bytes. The writer owns the file and ring, so
    switching between recording and idle cannot mix two dictation files.
    """

    def __init__(self, mic, preroll_ms):
        self.mic = mic
        self.max_bytes = int(RATE * SAMPLE_BYTES * preroll_ms / 1000)
        self.ring = deque()
        self.ring_bytes = 0
        self.lock = threading.Lock()
        self.outfile = None
        self.queue = queue.Queue()
        self.stream = None
        self.proc = None
        self.thread = None
        self.reader = None
        self.resampler = None
        self.abandoned = False
        self.closed = False
        self.bt_before = {}
        self.device_id = None

    def open(self):
        if sys.platform == "darwin" and mac_backend() != "ffmpeg":
            sd = _sd()
            if sd is None:
                return False
            if not self.mic or self.mic == "default":
                from . import coreaudio
                self.device_id = coreaudio.default_input_device()
            device = _mac_device(self.mic)
            rate = _mac_device_rate(device) if mac_backend() == "sdnative" else RATE
            try:
                self.resampler = _Polyphase(rate, RATE) if rate != RATE else None
                self.stream = sd.RawInputStream(
                    samplerate=rate, channels=1, dtype="int16", device=device,
                    callback=lambda data, _frames, _time, _status:
                        self.queue.put(bytes(data)) if not self.closed else None)
                self.stream.start()
            except Exception:
                if self.stream is not None:
                    _close_stream_quiet(self.stream)
                self.stream = None
                return False
        else:
            cmd = record_command(self.mic)
            if cmd is None:
                return False
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL)
            except OSError:
                return False
            self.reader = threading.Thread(target=self._read_pipe, daemon=True,
                                           name="audio-preroll-reader")
            self.reader.start()
            if sys.platform.startswith("linux"):
                threading.Thread(target=self._profile_snapshot, daemon=True,
                                 name="audio-profile-snapshot").start()
        self.thread = threading.Thread(target=self._write, daemon=True,
                                       name="audio-preroll-writer")
        self.thread.start()
        return True

    def device_changed(self):
        if sys.platform != "darwin" or self.mic not in (None, "default"):
            return False
        from . import coreaudio
        return coreaudio.default_input_device() != self.device_id

    def _profile_snapshot(self):
        before = _bluez_profiles()
        with self.lock:
            if self.proc is not None:
                self.bt_before = before
                return
        if before:
            _restore_bluez_profiles(before)

    def _read_pipe(self):
        proc = self.proc
        try:
            while proc is not None:
                data = proc.stdout.read(3200)
                if not data:
                    break
                self.queue.put(data)
        except OSError:
            pass

    def _write(self):
        while True:
            data = self.queue.get()
            if data is None:
                return
            if isinstance(data, threading.Event):
                data.set()
                continue
            if self.resampler is not None:
                data = self.resampler.feed(data)
            if not data:
                continue
            with self.lock:
                if self.outfile is not None:
                    self.outfile.write(data)
                    self.outfile.flush()
                else:
                    self.ring.append(data)
                    self.ring_bytes += len(data)
                    while self.ring_bytes > self.max_bytes and self.ring:
                        extra = self.ring_bytes - self.max_bytes
                        first = self.ring.popleft()
                        if len(first) > extra:
                            self.ring.appendleft(first[extra:])
                            self.ring_bytes -= extra
                        else:
                            self.ring_bytes -= len(first)

    def begin(self, path):
        self._flush_pending()
        try:
            outfile = open(path, "wb")
        except OSError:
            return False
        with self.lock:
            for data in self.ring:
                outfile.write(data)
            self.ring.clear()
            self.ring_bytes = 0
            outfile.flush()
            self.outfile = outfile
        return True

    def end(self):
        self._flush_pending()
        with self.lock:
            outfile, self.outfile = self.outfile, None
            if outfile is not None:
                outfile.close()

    def _flush_pending(self):
        if self.thread is not None and self.thread.is_alive():
            done = threading.Event()
            self.queue.put(done)
            done.wait(1)

    def close(self):
        self.end()
        self.closed = True
        stream, self.stream = self.stream, None
        if stream is not None:
            closer = threading.Thread(target=_close_stream_quiet, args=(stream,),
                                      daemon=True, name="mac-preroll-stop")
            closer.start()
            closer.join(timeout=MAC_STOP_TIMEOUT)
            self.abandoned = closer.is_alive()
        proc, self.proc = self.proc, None
        if proc is not None:
            proc.send_signal(2)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            proc.stdout.close()
        if self.reader is not None:
            self.reader.join(timeout=1)
        self.queue.put(None)
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.bt_before:
            _restore_bluez_profiles(self.bt_before)


class Recorder:
    def __init__(self, raw_path=RAW, preroll_ms=None, preroll_keep_s=3.0):
        # raw_path: eigene Rohdatei (z.B. für den Wake-Listener), damit zwei
        # Recorder sich nicht dieselbe Datei überschreiben. Der Diktat-Pfad
        # nutzt zwei Dateien im Wechsel (siehe RAW_B), ein eigener Pfad bleibt
        # bei genau dieser einen.
        self._paths = [raw_path, RAW_B] if raw_path == RAW else [raw_path]
        self._slot = -1          # erster start() nimmt wieder die vorderste
        self.raw_path = raw_path  # gerade bzw. zuletzt beschriebene Datei
        self.last_path = raw_path  # von stop() zuletzt geschlossene Datei
        self.running_path = None  # Datei der LAUFENDEN Aufnahme (None = keine)
        self.proc = None
        self.mac = None          # _MacStream, wenn macOS ohne ffmpeg aufnimmt
        self.outfile = None
        self.started = 0.0
        self._bt_before = {}
        self._abandoned = False  # macOS: ein Stream musste aufgegeben werden
        if preroll_ms is None:
            preroll_ms = os.environ.get("QUASSEL_PREROLL_MS", "0") if raw_path == RAW else 0
        try:
            self.preroll_ms = max(0, min(2000, int(preroll_ms)))
        except (TypeError, ValueError):
            self.preroll_ms = 0
        self.preroll_keep_s = max(0.1, float(preroll_keep_s))
        self._buffer = None
        self._warm = None
        self._warm_timer = None
        # Schützt NUR den Zustandswechsel (wer besitzt Stream/Prozess), nicht das
        # eigentliche Beenden: der Not-Aus kann aus einem anderen Thread in ein
        # laufendes stop() fallen und darf dabei weder warten noch auf ein
        # bereits abgeräumtes self.mac zugreifen.
        self._lock = threading.Lock()

    @property
    def stream_abandoned(self):
        """macOS: Musste ein Aufnahme-Stream aufgegeben werden? Dann steckt ein
        CoreAudio-Mutex fest, und der nächste Start liefe mit hoher
        Wahrscheinlichkeit in dieselbe Verklemmung — der Daemon startet sich
        deshalb neu. Auf Linux und Windows immer False."""
        return self._abandoned

    @property
    def active(self):
        if self._buffer is not None:
            return True
        if self.mac is not None:
            return self.mac.active
        return self.proc is not None and self.proc.poll() is None

    def start(self, mic="default"):
        os.makedirs(RUNDIR, exist_ok=True)
        # Ein Vorgänger, den niemand beendet hat (verlorenes stop(), Not-Aus
        # mitten im Ablauf), wird zuerst abgeräumt: eine fallen gelassene
        # Referenz nähme das Mikrofon mit und schriebe still weiter.
        if self.mac is not None or self.proc is not None or self._buffer is not None:
            _log("audio: vorherige Aufnahme lief noch -> wird zuerst beendet")
            self.stop()
        # Nächste Rohdatei wählen: die vorige gehört noch dem vorherigen Diktat,
        # dessen finish() sie unter Umständen erst noch ausliest.
        self._slot = (self._slot + 1) % len(self._paths)
        self.raw_path = self._paths[self._slot]
        if self.preroll_ms:
            with self._lock:
                capture, self._warm = self._warm, None
                timer, self._warm_timer = self._warm_timer, None
            if timer is not None:
                timer.cancel()
            if capture is not None and (capture.mic != mic or capture.device_changed()):
                capture.close()
                capture = None
            if capture is None:
                capture = _PrerollCapture(mic, self.preroll_ms)
                if not capture.open():
                    return False
            if not capture.begin(self.raw_path):
                capture.close()
                return False
            with self._lock:
                self._buffer = capture
                self.running_path = self.raw_path
                self.started = time.monotonic()
            return True
        if sys.platform == "darwin" and mac_backend() != "ffmpeg":
            mac = _MacStream(self.raw_path, native=mac_backend() == "sdnative")
            if not mac.start(mic):
                return False
            with self._lock:
                self.mac = mac
                self.running_path = self.raw_path
                self.started = mac.started
            return True
        cmd = record_command(mic)
        if cmd is None:
            return False
        outfile = open(self.raw_path, "wb")
        try:
            proc = subprocess.Popen(
                cmd, stdout=outfile, stderr=subprocess.DEVNULL)
        except OSError:
            outfile.close()
            return False
        with self._lock:
            self.outfile = outfile
            self.proc = proc
            self.running_path = self.raw_path
            self.started = time.monotonic()
        # pactl may take seconds. The capture must already be running while
        # its Bluetooth profile snapshot is collected.
        def profile_snapshot():
            before = _bluez_profiles()
            with self._lock:
                if self.proc is proc:
                    self._bt_before = before
                    return
            if before:
                _restore_bluez_profiles(before)
        threading.Thread(target=profile_snapshot, name="audio-profile-snapshot",
                         daemon=True).start()
        return True

    def raw_bytes(self, path=None):
        """Rohdaten lesen. Ohne path die gerade laufende Aufnahme (Live-Vorschau,
        Wake-Listener); mit path genau diese Datei — so bekommt ein wartendes
        finish() die Bytes SEINES Diktats, auch wenn inzwischen ein neues auf der
        anderen Rohdatei begonnen hat."""
        try:
            with open(path or self.raw_path, "rb") as f:
                data = f.read()
            return data[:len(data) - (len(data) % SAMPLE_BYTES)]
        except OSError:
            return b""

    def stop(self):
        """Aufnahme beenden. Rückgabe (und self.last_path): die gerade
        geschlossene Rohdatei — der Aufrufer reicht sie an raw_bytes() weiter
        und liest damit sicher sein eigenes Diktat.

        Genannt wird die Datei, die beim START dieser Aufnahme festgelegt wurde
        (running_path), nicht die, die im Moment des Aufrufs gerade aktuell
        ist: die Zusicherung gehört damit der Klasse und nicht dem Aufrufer.

        Der Besitz von Stream und Prozess wird unter dem Lock übernommen, das
        Beenden selbst läuft danach ohne ihn: fällt der Not-Aus aus einem
        anderen Thread hier hinein, findet er nichts mehr vor und kehrt sofort
        zurück, statt auf einem halb abgeräumten Zustand zu arbeiten."""
        with self._lock:
            path = self.last_path = self.running_path or self.raw_path
            self.running_path = None
            capture, self._buffer = self._buffer, None
            mac, self.mac = self.mac, None
            proc, self.proc = self.proc, None
            outfile, self.outfile = self.outfile, None
            bt_before, self._bt_before = self._bt_before, {}
        if capture is not None:
            capture.end()
            with self._lock:
                self._warm = capture
                timer = threading.Timer(self.preroll_keep_s, self._expire_warm,
                                        args=(capture,))
                timer.daemon = True
                self._warm_timer = timer
                timer.start()
            return path
        if mac is not None:
            mac.stop()
            if mac._abandoned:
                self._abandoned = True
            return path
        if proc is None:
            return path
        proc.send_signal(2)  # SIGINT
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if outfile:
            outfile.close()
        if bt_before:
            _restore_bluez_profiles(bt_before)
        return path

    def _expire_warm(self, capture):
        with self._lock:
            if self._warm is not capture:
                return
            self._warm = None
            self._warm_timer = None
            capture.close()
            if capture.abandoned:
                self._abandoned = True
