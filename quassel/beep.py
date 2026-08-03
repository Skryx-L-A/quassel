"""Kurze Töne für Aufnahme-Start/-Ende (plattformneutral, optional abschaltbar).

Zwei UNTERSCHIEDLICHE Töne: aufsteigend = "du kannst sprechen" (Start),
absteigend = "fertig, Aufnahme aus" (Stop). Bewusst leise + kurz. Das Abspielen
ist nicht-blockierend und schlägt leise fehl, wenn kein Player/Datei da ist —
ein Ton darf das Diktat nie aufhalten.

Linux: pw-play / paplay / aplay (das erste vorhandene). Windows: winsound.
macOS: eigener Weg über einen warmgehaltenen Ausgabestrom (_MacPlayer),
afplay nur noch als Rückfall.
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import wave

from . import audio


def _sounds_dir():
    mei = getattr(sys, "_MEIPASS", None)        # PyInstaller-Bundle
    cands = []
    if mei:
        cands.append(os.path.join(mei, "assets", "sounds"))
    cands.append(os.path.join(os.path.dirname(__file__), "..", "assets", "sounds"))
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[-1]


_DIR = _sounds_dir()
START_WAV = os.path.join(_DIR, "start.wav")
STOP_WAV = os.path.join(_DIR, "stop.wav")


# --------------------------------------------------------------- macOS-Weg
# Über Bluetooth (A2DP) schläft die Funkstrecke im Leerlauf ein; die ersten
# paar hundert Millisekunden neuer Wiedergabe gehen beim Wiederanlaufen
# verloren. Beide Töne sind kürzer als das (0,160 s und 0,170 s), der Startton
# fiel deshalb regelmäßig komplett in dieses Loch — der Stoppton kam Sekunden
# später auf einer bereits wachen Strecke an und war da. Gemessen: afplay
# selbst scheitert nie, das Gerät wacht in 12 bis 18 ms auf.
#
# Gegenmittel: ein eigener Ausgabestrom, der nach einem Ton offen bleibt (dann
# läuft die Strecke über ein Diktat hinweg durch), plus Vorlauf-Stille, wenn
# er doch kalt geöffnet werden musste.
WARM_KEEP = 15.0      # s ohne Ton, danach wird der Ausgabestrom geschlossen
PREROLL_MS = 350      # ms Stille vor einem Ton auf frisch geöffnetem Strom
OUT_RATE = 16000      # beide Töne liegen als 16-kHz-Mono vor, CoreAudio wandelt
STOP_TIMEOUT = 2.0    # Frist beim Schließen des Ausgabestroms
QUEUE_MAX = 8         # hängt die Wiedergabe, sind wartende Töne ohnehin wertlos


def _sd():
    """sounddevice-Modul oder None. Eigener Zugang, damit Tests hier eine
    Attrappe einhängen können, ohne den Aufnahmepfad anzufassen."""
    return audio._sd()


def _afplay(path):
    """Rückfallweg: der alte Subprozess-Player."""
    try:
        subprocess.Popen(["afplay", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:    # noqa: BLE001 — Ton darf nie stören
        pass


def _read_wav(path):
    """(int16-Mono-Bytes, Rate) oder (None, 0). Fremde Formate (Stereo, 8 oder
    24 Bit) nimmt der eigene Weg nicht an, sie gehen über afplay."""
    try:
        with wave.open(path, "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None, 0
            return w.readframes(w.getnframes()), w.getframerate()
    except Exception:    # noqa: BLE001 — kaputte oder fremde Datei
        return None, 0


def _device_out_rate(sd):
    """Native Abtastrate des Ausgabegeräts (Fallback: 48 kHz)."""
    try:
        info = sd.query_devices(sd.default.device[1])
        rate = int(round(info["default_samplerate"]))
        return rate if rate > 0 else 48000
    except Exception:    # noqa: BLE001 — Gerät weg oder Backend kaputt
        return 48000


class _MacPlayer:
    """Spielt die Töne in-Prozess über einen warmgehaltenen Ausgabestrom.

    start()/stop() kommen aus dem Ereignis-Thread des Hotkeys und dürfen nicht
    warten: sie reihen den Ton nur ein, abgespielt wird in einem eigenen
    Thread. Die WAV-Dateien werden einmal gelesen und im Speicher gehalten
    (wenige Kilobyte).
    """

    def __init__(self):
        self._queue = queue.Queue(maxsize=QUEUE_MAX)
        self._thread = None
        self._lock = threading.Lock()      # schützt nur den Thread-Start
        self._stream = None
        self._rate = OUT_RATE
        self._cache = {}                   # (Pfad, Rate) -> int16-Bytes

    def play(self, path):
        """Aus dem Aufrufer-Thread: einreihen und sofort zurückkehren."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="beep-out")
                self._thread.start()
        try:
            self._queue.put_nowait(path)
        except queue.Full:                 # Wiedergabe hängt -> Ton verwerfen
            pass

    def close(self):
        """Geordneter Abbau: Abspiel-Thread beenden, Strom schließen. Im
        Betrieb läuft der Thread einfach weiter, gebraucht wird das beim
        Aufräumen (Tests)."""
        t = self._thread
        if t is None or not t.is_alive():
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            return
        t.join(timeout=STOP_TIMEOUT + 1.0)

    def _run(self):
        while True:
            # Ohne offenen Strom gibt es nichts warmzuhalten, dann wird ohne
            # Frist gewartet.
            timeout = WARM_KEEP if self._stream is not None else None
            try:
                path = self._queue.get(timeout=timeout)
            except queue.Empty:            # WARM_KEEP ohne Ton
                self._close_stream()
                continue
            if path is None:               # Abbau-Zeichen aus close()
                self._close_stream()
                break
            try:
                self._play_one(path)
            except Exception:              # noqa: BLE001 — Ton darf nie stören
                pass

    def _play_one(self, path):
        fresh = self._ensure_stream()
        data = self._samples(path) if self._stream is not None else None
        if data is None:
            _afplay(path)                  # kein Strom oder fremdes Format
            return
        try:
            if fresh and PREROLL_MS > 0:
                # Vorlauf nur im kalten Fall: er weckt die Funkstrecke, damit
                # der Ton selbst nicht mehr in ihr Anlaufloch fällt.
                frames = int(self._rate * PREROLL_MS / 1000.0)
                self._stream.write(b"\x00\x00" * frames)
            self._stream.write(data)
        except Exception:                  # noqa: BLE001 — Gerät gewechselt o.ä.
            # Dieser Ton ist verloren; der nächste öffnet wieder frisch.
            self._close_stream()

    def _ensure_stream(self):
        """Öffnet den Ausgabestrom, falls nötig. True = für diesen Ton frisch
        geöffnet, es braucht also den Vorlauf."""
        if self._stream is not None:
            return False
        sd = _sd()
        if sd is None:
            return False
        rates = [OUT_RATE]
        dev_rate = _device_out_rate(sd)
        if dev_rate != OUT_RATE:
            rates.append(dev_rate)         # 16 kHz abgelehnt -> Gerätevorgabe
        for rate in rates:
            try:
                stream = sd.RawOutputStream(samplerate=rate, channels=1,
                                            dtype="int16")
                stream.start()
            except Exception:              # noqa: BLE001 — Rate oder Gerät nicht nutzbar
                continue
            self._stream, self._rate = stream, rate
            return True
        return False

    def _samples(self, path):
        """int16-Bytes des Tons in der Rate des offenen Stroms, oder None."""
        key = (path, self._rate)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data, rate = _read_wav(path)
        if data is None:
            return None
        if rate != self._rate:
            try:
                data = audio._Polyphase(rate, self._rate).feed(data)
            except Exception:              # noqa: BLE001 — ohne numpy kein Resampling
                return None
        self._cache[key] = data
        return data

    def _close_stream(self):
        """Strom schließen, aber mit harter Frist — dieselbe Disziplin wie im
        Aufnahmepfad (audio._MacStream._halt_stream): kehrt CoreAudio nicht
        zurück, wird der Strom aufgegeben statt den Abspiel-Thread zu
        blockieren. Der nächste Ton öffnet dann einen neuen."""
        stream, self._stream = self._stream, None
        if stream is None:
            return
        t = threading.Thread(target=audio._close_stream_quiet, args=(stream,),
                             daemon=True, name="beep-stream-stop")
        t.start()
        t.join(timeout=STOP_TIMEOUT)
        if t.is_alive():
            audio._log("beep: Ausgabestrom reagiert nicht (>%.1fs) -> aufgegeben"
                       % STOP_TIMEOUT)


_PLAYER = None
_PLAYER_LOCK = threading.Lock()


def _player():
    global _PLAYER
    with _PLAYER_LOCK:
        if _PLAYER is None:
            _PLAYER = _MacPlayer()
        return _PLAYER


def _play(path):
    if not path or not os.path.exists(path):
        return
    try:
        if os.name == "nt":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        if sys.platform == "darwin":
            _player().play(path)
            return
        for player in (["pw-play"], ["paplay"], ["aplay", "-q"]):
            if shutil.which(player[0]):
                subprocess.Popen(player + [path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    except Exception:        # noqa: BLE001 — Ton darf nie stören
        pass


def start():
    """Aufsteigender Ton: Aufnahme bereit, jetzt sprechen."""
    _play(START_WAV)


def stop():
    """Absteigender Ton: Aufnahme beendet."""
    _play(STOP_WAV)
