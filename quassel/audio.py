"""Audio-Aufnahme (PipeWire/Pulse/CoreAudio) und WAV-Verpackung.

Aufgenommen wird rohes PCM (s16le, 16 kHz, mono) in eine Rohdatei — daraus
lassen sich während der Aufnahme Teilstücke für die Live-Vorschau schneiden.

Linux und Windows füllen die Datei aus einem Aufnahme-Subprozess
(pw-record/parecord), macOS in-process über einen sounddevice-Stream — die
Bytes-Schnittstelle (raw_bytes()) ist in beiden Fällen dieselbe.
"""
import json
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


def rms_level(window_s=0.15):
    """Pegel 0.0–1.0 aus dem Ende der laufenden Roh-Aufnahme (für die Pille)."""
    raw = newest_raw()
    try:
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


class Recorder:
    def __init__(self, raw_path=RAW):
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
        if self.mac is not None:
            return self.mac.active
        return self.proc is not None and self.proc.poll() is None

    def start(self, mic="default"):
        os.makedirs(RUNDIR, exist_ok=True)
        # Ein Vorgänger, den niemand beendet hat (verlorenes stop(), Not-Aus
        # mitten im Ablauf), wird zuerst abgeräumt: eine fallen gelassene
        # Referenz nähme das Mikrofon mit und schriebe still weiter.
        if self.mac is not None or self.proc is not None:
            _log("audio: vorherige Aufnahme lief noch -> wird zuerst beendet")
            self.stop()
        # Nächste Rohdatei wählen: die vorige gehört noch dem vorherigen Diktat,
        # dessen finish() sie unter Umständen erst noch ausliest.
        self._slot = (self._slot + 1) % len(self._paths)
        self.raw_path = self._paths[self._slot]
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
        bt_before = _bluez_profiles()
        outfile = open(self.raw_path, "wb")
        proc = subprocess.Popen(
            cmd, stdout=outfile, stderr=subprocess.DEVNULL)
        with self._lock:
            self._bt_before = bt_before
            self.outfile = outfile
            self.proc = proc
            self.running_path = self.raw_path
            self.started = time.monotonic()
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
            mac, self.mac = self.mac, None
            proc, self.proc = self.proc, None
            outfile, self.outfile = self.outfile, None
            bt_before, self._bt_before = self._bt_before, {}
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
