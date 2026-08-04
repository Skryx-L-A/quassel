"""Kurze Töne für Aufnahme-Start/-Ende (plattformneutral, optional abschaltbar).

Zwei UNTERSCHIEDLICHE Töne: aufsteigend = "du kannst sprechen" (Start),
absteigend = "fertig, Aufnahme aus" (Stop). Bewusst leise + kurz. Das Abspielen
ist nicht-blockierend und schlägt leise fehl, wenn kein Player/Datei da ist —
ein Ton darf das Diktat nie aufhalten.

Linux: pw-play / paplay / aplay (das erste vorhandene). Windows: winsound.
macOS: eigener Weg über eine warmgehaltene Default-Output-AudioUnit
(_MacPlayer), afplay nur noch als Rückfall.
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave

from . import audio, coreaudio


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
# WELCHES Gerät (2026-08-04): die Töne gehen auf das System-Standard-Ausgabegerät
# und folgen ihm zur Laufzeit — dorthin also, wohin macOS gerade jeden anderen
# Ton auch schickt. Bis v2.6.0 lief die Ausgabe über PortAudio, das sich das
# Standardgerät beim Initialisieren des Prozesses merkt und danach nie wieder
# hinsieht: wer seine Kopfhörer erst nach dem Start verband, hörte die Töne für
# den Rest der Sitzung aus den eingebauten Lautsprechern. Die Begründung und die
# Messwerte stehen in quassel/coreaudio.py.
#
# Wer die Töne bewusst woanders haben will, wählt im Kontrollzentrum ein festes
# Gerät; set_output() nimmt den Namen entgegen.
#
# Über Bluetooth (A2DP) schläft die Funkstrecke im Leerlauf ein; die ersten
# paar hundert Millisekunden neuer Wiedergabe gehen beim Wiederanlaufen
# verloren. Beide Töne sind kürzer als das (0,160 s und 0,170 s), der Startton
# fiel deshalb regelmäßig komplett in dieses Loch — der Stoppton kam Sekunden
# später auf einer bereits wachen Strecke an und war da. Gemessen: afplay
# selbst scheitert nie, das Gerät wacht in 12 bis 18 ms auf.
#
# Gegenmittel: eine eigene Ausgabe-Einheit, die nach einem Ton offen bleibt
# (dann läuft die Strecke über ein Diktat hinweg durch), plus Vorlauf-Stille,
# wenn sie doch kalt geöffnet werden musste.
#
# Die Einheit läuft KONTINUIERLICH über einen Render-Callback, der immer
# Material liefert: anstehende Tonproben, den Rest mit Null aufgefüllt. Ein
# früherer Umbau schrieb nur die 0,17 s Ton in einen Puffer, den PortAudio mit
# der Vorgabe 'high' fast eine Sekunde lang anlegt (gemessen 0,993 s), und
# danach nichts mehr — der Puffer lief leer, und die Unterläufe waren als kurzes
# "Pfft" statt des Stopptons zu hören. Mit dem Callback kann strukturell nichts
# mehr leerlaufen, weder am Tonende noch während der Warmhaltezeit.
WARM_KEEP = 60.0      # s ohne Ton bis zum Schließen: über eine Arbeitssitzung
                      # bleibt die Strecke wach, Vorlauf fällt nur nach echter Pause an
PREROLL_MS = 250      # ms Stille vor einem Ton auf frisch geöffneter Einheit: weckt
                      # die Funkstrecke (12-18 ms) und verzögert den Ton so wenig wie möglich
OUT_RATE = 16000      # beide Töne liegen als 16-kHz-Mono vor, CoreAudio wandelt
STOP_TIMEOUT = 2.0    # Frist beim Schließen der Ausgabe-Einheit
QUEUE_MAX = 8         # hängt die Wiedergabe, sind wartende Töne ohnehin wertlos
DEAD_AFTER = 0.5      # s ohne einen einzigen Render-Aufruf seit dem letzten Ton, ab
                      # denen eine offene Einheit als tot gilt. Eine laufende meldet
                      # sich binnen Millisekunden; die Frist ist nur da, damit zwei
                      # schnell aufeinanderfolgende Töne die noch anlaufende Einheit
                      # nicht für tot erklären und den ersten Ton mitreißen.


def _open_unit(callback, rate, device):
    """Laufende Default-Output-AudioUnit oder Ausnahme. Eigener Zugang, damit
    Tests hier eine Attrappe einhängen können, ohne echte Geräte zu öffnen."""
    unit = coreaudio.DefaultOutputUnit(callback, rate, device)
    try:
        unit.start()
    except Exception:
        # Die Einheit ist zu diesem Zeitpunkt schon scharf (AudioUnitInitialize
        # lief durch), nur das Starten scheiterte. Ohne dieses close() bliebe
        # bei jedem Versuch eine AudioComponentInstance stehen.
        unit.close()
        raise
    return unit


def list_outputs():
    """[(Name, Beschreibung)] der Ausgabegeräte für das Kontrollzentrum.
    Außerhalb von macOS leer: dort nehmen die Abspielwege kein Gerät entgegen."""
    if sys.platform != "darwin":
        return []
    return [(name, name) for _dev, name in coreaudio.output_devices()]


def set_output(name):
    """Zielgerät für die Töne setzen: "system" (oder leer) folgt dem
    System-Standard, sonst der Gerätename aus list_outputs()."""
    if sys.platform == "darwin":
        _player().set_device(name)


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


class _MacPlayer:
    """Spielt die Töne in-Prozess über eine warmgehaltene Ausgabe-Einheit.

    start()/stop() kommen aus dem Ereignis-Thread des Hotkeys und dürfen nicht
    warten: sie reihen den Ton nur ein, abgespielt wird in einem eigenen
    Thread. Die WAV-Dateien werden einmal gelesen und im Speicher gehalten
    (wenige Kilobyte).

    Die Einheit wird nicht beschrieben, sondern vom Render-Callback bedient:
    der Abspiel-Thread legt die Proben in `_pending`, der Callback holt sie
    blockweise ab und füllt den Rest mit Stille. Beide teilen sich `_pending`
    unter einem kurzen Lock — der Callback darf nie warten.
    """

    def __init__(self):
        self._queue = queue.Queue(maxsize=QUEUE_MAX)
        self._thread = None
        self._lock = threading.Lock()      # schützt nur den Thread-Start
        self._unit = None
        self._cache = {}                   # Pfad -> int16-Bytes in OUT_RATE
        self._pending = bytearray()        # noch nicht abgeholte Tonproben
        self._pending_lock = threading.Lock()
        self._rendered = 0                 # Callback-Aufrufe der offenen Einheit
        self._rendered_at_tone = 0         # Stand davon beim letzten Ton
        self._last_tone_at = 0.0           # wann der letzte Ton eingelegt wurde
        self._device = "system"            # Wunschgerät, "system" = Standard folgen
        self._open_device = None           # AudioDeviceID, mit der die Einheit läuft
        self._missing_logged = None        # zuletzt als fehlend gemeldetes Gerät
        self._open_failed_logged = False   # eine Zeile je Ausfall, nicht je Ton

    def set_device(self, name):
        """Wunschgerät merken. Ein Wechsel wirkt beim nächsten Ton; die Einheit
        wird dann neu geöffnet, statt weiter auf dem alten Gerät zu spielen."""
        self._device = name or "system"

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
        """Geordneter Abbau: Abspiel-Thread beenden, Einheit schließen. Im
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
            # Ohne offene Einheit gibt es nichts warmzuhalten, dann wird ohne
            # Frist gewartet.
            timeout = WARM_KEEP if self._unit is not None else None
            try:
                path = self._queue.get(timeout=timeout)
            except queue.Empty:            # WARM_KEEP ohne Ton
                self._close_unit()
                continue
            if path is None:               # Abbau-Zeichen aus close()
                self._close_unit()
                break
            try:
                self._play_one(path)
            except Exception:              # noqa: BLE001 — Ton darf nie stören
                pass

    def _callback(self, out):
        """Audio-Thread: nimmt, was anliegt, füllt den Rest mit Stille und
        kehrt sofort zurück. Kein Warten, kein Logging."""
        self._rendered += 1
        need = len(out)
        with self._pending_lock:
            chunk = bytes(self._pending[:need])
            del self._pending[:len(chunk)]
        if len(chunk) < need:
            chunk += b"\x00" * (need - len(chunk))
        out[:need] = chunk

    def _play_one(self, path):
        fresh = self._ensure_unit()
        data = self._samples(path) if self._unit is not None else None
        if data is None:
            # Keine Einheit oder fremdes Format: afplay startet einen eigenen
            # Prozess und trifft damit ebenfalls das aktuelle Standardgerät.
            _afplay(path)
            return
        if fresh and PREROLL_MS > 0:
            # Vorlauf nur im kalten Fall: er weckt die Funkstrecke, damit
            # der Ton selbst nicht mehr in ihr Anlaufloch fällt.
            frames = int(OUT_RATE * PREROLL_MS / 1000.0)
            data = b"\x00\x00" * frames + data
        with self._pending_lock:
            self._pending.extend(data)
        # Merker für die Lebendigkeitsprüfung: ab hier MUSS die Einheit Blöcke
        # abholen, sonst hat sie das Gerät unter sich verloren.
        self._rendered_at_tone = self._rendered
        self._last_tone_at = time.monotonic()

    def _wanted_device(self):
        """AudioDeviceID, an die die Einheit gebunden werden soll, oder None
        für „dem System-Standard folgen". Ein eingestelltes, aber nicht mehr
        vorhandenes Gerät fällt auf den Standard zurück — lieber der falsche
        Lautsprecher als gar kein Ton."""
        name = self._device
        if not name or name == "system":
            self._missing_logged = None
            return None
        dev = coreaudio.find_output_device(name)
        if dev is None:
            if self._missing_logged != name:
                self._missing_logged = name
                audio._log("beep: Ausgabegerät %r nicht vorhanden -> "
                           "System-Standard" % name)
            return None
        self._missing_logged = None
        return dev

    def _ensure_unit(self):
        """Öffnet die Ausgabe-Einheit, falls nötig. True = für diesen Ton frisch
        geöffnet, es braucht also den Vorlauf."""
        want = self._wanted_device()
        if self._unit is not None:
            if self._stale(want):
                self._close_unit()
            else:
                return False
        try:
            self._unit = _open_unit(self._callback, OUT_RATE, want)
        except Exception as exc:           # noqa: BLE001 — kein Gerät, kein CoreAudio
            if not self._open_failed_logged:
                # Eine Zeile, solange der Ausfall anhält: sonst schriebe jedes
                # Diktat zwei weitere in ein Protokoll, das niemand mehr liest.
                self._open_failed_logged = True
                audio._log("beep: Ausgabe-Einheit nicht zu öffnen (%r) -> afplay" % exc)
            self._unit = None
            return False
        self._open_device = want
        self._rendered = 0
        self._rendered_at_tone = 0
        self._last_tone_at = time.monotonic()
        self._open_failed_logged = False
        return True

    def _stale(self, want):
        """Taugt die offene Einheit noch für den nächsten Ton?

        Drei Gründe dagegen: sie läuft nicht mehr, das Wunschgerät hat sich
        geändert, oder sie hat seit dem letzten Ton KEINEN Block mehr abgeholt.
        Der letzte Fall ist der stille: ein Gerät, das verschwindet, während die
        Einheit daran hängt, hört einfach auf zu fragen — die Einheit meldet
        sich weiter als aktiv, und die Töne verschwänden ohne diese Prüfung
        wortlos in `_pending`. Verlangt wird deshalb FORTSCHRITT seit dem
        letzten Ton, nicht nur „überhaupt schon einmal gerendert": ein Zähler,
        der bei tausend stehenbleibt, ist genauso tot wie einer, der bei null
        steht.

        Die Prüfung greift einen Ton zu spät: fällt das Gerät zwischen zwei
        Tönen weg, gab es seit dem letzten Ton noch Fortschritt, und dieser eine
        Ton geht verloren. Früher ginge es nur, wenn der Abspiel-Thread nach dem
        Einlegen auf den ersten Render-Aufruf wartete — das legt einen Ton lang
        die Wiedergabe still, um im Regelfall nichts zu gewinnen. Ein verlorener
        Ton gegen eine Minute Stille ist der Handel, der hier gemacht wird.

        Dass der System-Standard wechselt, ist KEIN Grund — dem folgt die
        Einheit von selbst (siehe quassel/coreaudio.py)."""
        if not getattr(self._unit, "active", True):
            return True
        if want != self._open_device:
            return True
        if (self._rendered <= self._rendered_at_tone
                and time.monotonic() - self._last_tone_at > DEAD_AFTER):
            audio._log("beep: Ausgabe-Einheit liefert nichts mehr -> neu geöffnet")
            return True
        return False

    def _samples(self, path):
        """int16-Bytes des Tons in OUT_RATE, oder None."""
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        data, rate = _read_wav(path)
        if data is None:
            return None
        if rate != OUT_RATE:
            try:
                data = audio._Polyphase(rate, OUT_RATE).feed(data)
            except Exception:              # noqa: BLE001 — ohne numpy kein Resampling
                return None
        self._cache[path] = data
        return data

    def _close_unit(self):
        """Einheit schließen, aber mit harter Frist — dieselbe Disziplin wie im
        Aufnahmepfad (audio._MacStream._halt_stream): kehrt CoreAudio nicht
        zurück, wird die Einheit aufgegeben statt den Abspiel-Thread zu
        blockieren. Der nächste Ton öffnet dann eine neue."""
        unit, self._unit = self._unit, None
        self._open_device = None
        with self._pending_lock:           # Reste gehören zur alten Einheit
            del self._pending[:]
        if unit is None:
            return
        t = threading.Thread(target=_close_unit_quiet, args=(unit,),
                             daemon=True, name="beep-unit-stop")
        t.start()
        t.join(timeout=STOP_TIMEOUT)
        if t.is_alive():
            audio._log("beep: Ausgabe-Einheit reagiert nicht (>%.1fs) -> aufgegeben"
                       % STOP_TIMEOUT)


def _close_unit_quiet(unit):
    try:
        unit.close()
    except Exception:        # noqa: BLE001 — Abbau darf nie stören
        pass


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
