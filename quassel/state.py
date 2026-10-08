"""Laufzeit-Dateien + State-IPC Daemon → Pille (state.json, atomar)."""
import json
import math
import os
import sys
import threading
import time

from . import profile

if os.name == "nt":
    RUNDIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                          profile.dirname("Quassel"), "run")
elif sys.platform == "darwin":
    # macOS kennt kein XDG_RUNTIME_DIR. Nicht in ~/Library/Caches: das darf
    # das System bei Plattenplatzdruck jederzeit leeren — auch mitten in
    # einer laufenden Aufnahme (rec.raw).
    RUNDIR = os.path.expanduser(
        "~/Library/Application Support/" + profile.dirname("Quassel") + "/run")
else:
    XDG_RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    RUNDIR = os.path.join(XDG_RUNTIME, profile.dirname("quassel"))
RAW = os.path.join(RUNDIR, "rec.raw")
WAV = os.path.join(RUNDIR, "rec.wav")
PARTWAV = os.path.join(RUNDIR, "partial.wav")
STATE = os.path.join(RUNDIR, "state.json")


# Mikrofonpegel in state.json (H112): der Daemon schreibt ihn während einer
# Aufnahme höchstens LEVEL_HZ-mal je Sekunde mit, die Pille zeigt ihn an.
# "ts" bleibt dabei der Zeitpunkt des letzten Zustandswechsels — die Pille
# erkennt daran neue Zustände, ein Pegel-Update ist keiner.
LEVEL_HZ = 15
LEVEL_MAX_AGE = 0.5      # s: älterer Pegel gilt als ausgefallen
_lock = threading.Lock()
_doc = {"state": "idle", "text": "", "ts": 0.0}
_level_written = float("-inf")


def _write(doc):
    os.makedirs(RUNDIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, STATE)


def state_set(state, text=""):
    global _doc
    with _lock:
        doc = {"state": state, "text": text, "ts": time.time()}
        if state == "recording" and _doc.get("state") == "recording" \
                and "level" in _doc:
            # Vorschautext während der Aufnahme: der Pegel bleibt stehen
            doc["level"] = _doc["level"]
            doc["level_ts"] = _doc["level_ts"]
        _doc = doc
        _write(doc)


def level_set(level, now=None):
    """Pegel 0.0–1.0 der laufenden Aufnahme nach state.json schreiben (atomar).

    Gedrosselt auf LEVEL_HZ und nur im Zustand "recording" — der Aufrufer
    darf so oft rufen, wie er misst. True, wenn geschrieben wurde."""
    global _doc, _level_written
    now = time.monotonic() if now is None else now
    with _lock:
        if _doc.get("state") != "recording":
            return False
        if now - _level_written < 1.0 / LEVEL_HZ:
            return False
        _level_written = now
        doc = dict(_doc)
        doc["level"] = round(max(0.0, min(1.0, float(level))), 4)
        doc["level_ts"] = time.time()
        _doc = doc
        _write(doc)
        return True


def state_read():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"state": "idle", "text": "", "ts": 0}


def level_read(st, now=None, max_age=LEVEL_MAX_AGE):
    """Pegel aus einem state_read()-Dokument, None ohne frischen Wert (kein
    Pegel gesendet, älterer Daemon, Aufnahme hängt)."""
    try:
        level = float(st["level"])
        age = (time.time() if now is None else now) - float(st["level_ts"])
    except (KeyError, TypeError, ValueError):
        return None
    if age > max_age:
        return None
    return max(0.0, min(1.0, level))


class LevelMeter:
    """Pegelverlauf einer Aufnahme für die Pille, gleich auf allen Plattformen.

    feed() bekommt je Takt den Wert von level_read(). Bis ein erster Pegel
    eintrifft, gilt die Quelle als unbekannt (seen False) — die Pille zeigt
    dann ihre bisherige Animation statt fälschlich „kein Ton". Fällt der
    Pegel danach aus, zählt das als Stille. silent() wird wahr, wenn seit
    SILENT_HINT_S nichts über SILENT_LEVEL kam: ein stummes, falsches oder
    nicht freigegebenes Mikrofon liefert Nullen."""

    BARS = 5
    SILENT_LEVEL = 0.0005     # rms_level-Skala: RMS 4 von 32767, ~ -78 dBFS
    SILENT_HINT_S = 1.5
    FLOOR_DB, RANGE_DB = -60.0, 48.0

    def __init__(self, now=None):
        self.reset(now)

    def reset(self, now=None):
        self.seen = False
        self.history = [0.0] * self.BARS
        self.smooth = 0.0
        self.quiet_since = time.monotonic() if now is None else now

    def feed(self, level, now=None):
        now = time.monotonic() if now is None else now
        if level is None:
            if not self.seen:
                return
            level = 0.0
        self.seen = True
        if level > self.SILENT_LEVEL:
            self.quiet_since = now
        frac = self.to_frac(level)
        self.history = self.history[1:] + [frac]
        self.smooth = max(frac, self.smooth * 0.8)

    def silent(self, now=None):
        now = time.monotonic() if now is None else now
        return self.seen and now - self.quiet_since >= self.SILENT_HINT_S

    @classmethod
    def to_frac(cls, level):
        """Pegel -> Balkenhöhe 0..1 auf einer dB-Skala (-60 bis -12 dBFS):
        Sprache füllt die Balken sichtbar, Raumrauschen bleibt flach."""
        if level <= 0:
            return 0.0
        dbfs = 20 * math.log10(level * 8000.0 / 32768.0)
        return max(0.0, min(1.0, (dbfs - cls.FLOOR_DB) / cls.RANGE_DB))

    REST = (0.30, 0.46, 0.38, 0.52, 0.34)

    def wave_fracs(self, recording, now=None, calm=False):
        """Balkenhöhen 0..1 für die Pille. Ruhend außerhalb der Aufnahme;
        bei Aufnahme der echte Pegel, sobald er ankommt, eine flache Null bei
        Stille; ohne Pegelquelle (älterer Daemon) die frühere Animation, bei
        calm ruhend."""
        if not recording:
            return list(self.REST)
        if self.seen:
            if self.silent(now):
                return [0.0] * self.BARS
            return [max(0.14, f) for f in self.bars(calm)]
        if calm:
            return list(self.REST)
        t = time.monotonic() if now is None else now
        return [0.22 + 0.78 * (0.5 + 0.5 * math.sin(t * 7.5 + i * 1.1))
                for i in range(self.BARS)]

    def bars(self, calm=False):
        """Höhen der fünf Balken: der Verlauf der letzten Messungen, bei
        calm (reduzierte Bewegung) ein ruhiges Profil um den geglätteten Wert."""
        if calm:
            return [self.smooth * k for k in (0.6, 0.85, 1.0, 0.85, 0.6)]
        return list(self.history)
