"""Konfiguration, Wörterbuch, Verlauf und Server-Umgebung von Quassel.

Dateien:
  ~/.config/quassel/config.ini      Einstellungen (hotkey/speech/pill/ui)
  ~/.config/quassel/server.env      SERVER_BIN + MODEL_PATH für quassel-server
  ~/.config/quassel/dictionary.txt  ein Wort pro Zeile (Erkennungs-Bias)
  ~/.local/share/quassel/history.jsonl  letzte Diktate (lokal, abschaltbar)
"""
import configparser
import io
import json
import math
import os
import sys
import threading

from . import fsutil, profile

if os.name == "nt":
    CONFDIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                           profile.dirname("Quassel"))
    DATADIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                           profile.dirname("Quassel"))
    SHARED_DATADIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                                  "Quassel")
elif sys.platform == "darwin":
    # macOS: Konfiguration + Daten (models/, history) unter Application Support
    CONFDIR = DATADIR = os.path.expanduser(
        "~/Library/Application Support/" + profile.dirname("Quassel"))
    SHARED_DATADIR = os.path.expanduser("~/Library/Application Support/Quassel")
else:
    CONFDIR = os.path.join(os.environ.get("XDG_CONFIG_HOME",
                                          os.path.expanduser("~/.config")), profile.dirname("quassel"))
    DATADIR = os.path.join(os.environ.get("XDG_DATA_HOME",
                                          os.path.expanduser("~/.local/share")), profile.dirname("quassel"))
    SHARED_DATADIR = os.path.join(os.environ.get("XDG_DATA_HOME",
                                                 os.path.expanduser("~/.local/share")), "quassel")
# Modelle sind groß und profilunabhängig: ein Profil liest die der Standardinstanz.
MODELDIR = os.path.join(SHARED_DATADIR, "models")
CONFIG = os.path.join(CONFDIR, "config.ini")
SERVERENV = os.path.join(CONFDIR, "server.env")
DICTIONARY = os.path.join(CONFDIR, "dictionary.txt")
REPLACEMENTS = os.path.join(CONFDIR, "replacements.txt")
AI_MODES = os.path.join(CONFDIR, "ai_modes.txt")
HISTORY = os.path.join(DATADIR, "history.jsonl")
HISTORY_MAX = 50
WAKEWORD_DEFAULT = "Hey Quassel"
OLLAMA_DEFAULT = "http://127.0.0.1:11434"

CHORDS = {
    "ctrl+meta": ({29, 97}, {125, 126}),   # Strg + Windows-Taste
    "alt+meta":  ({56, 100}, {125, 126}),  # Alt + Windows-Taste
    "ctrl+alt":  ({29, 97}, {56, 100}),    # Strg + Alt
}
CHORD_LABEL_KEYS = {"ctrl+meta": "chord_ctrl_meta", "alt+meta": "chord_alt_meta",
                    "ctrl+alt": "chord_ctrl_alt"}
# Volle + quantisierte (q5) Modelle. Quantisierte laufen auf CPU deutlich
# schneller bei nahezu gleicher Genauigkeit — die Wahl für schwächere Hardware.
# Alle liegen im selben HF-Repo unter ggml-<name>.bin, daher genügt der Name.
MODELS = ["tiny", "base", "base-q5_1", "small", "small-q5_1",
          "medium", "medium-q5_0", "large-v3-turbo", "large-v3-turbo-q5_0",
          "large-v3"]
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{}.bin"
# Silero-VAD-Modell (~0.9 MB): überspringt Stille -> schneller + verhindert
# Halluzinationen auf Stille (z.B. "Untertitel der Amara.org-Community").
VAD_MODEL_FILE = "ggml-silero-v5.1.2.bin"
VAD_MODEL_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/" + VAD_MODEL_FILE


def _new_parser():
    # interpolation=None: "%" ist sonst ein Sonderzeichen. Ein Wert wie eine
    # Wake-Phrase oder ein KI-Endpunkt mit "%" warf beim Speichern ValueError
    # und beim Lesen InterpolationSyntaxError.
    return configparser.ConfigParser(interpolation=None)


def _read_parser():
    """config.ini gelesen; bei unlesbarer oder kaputter Datei, was sich noch
    lesen liess (schlimmstenfalls nichts). Wirft nie."""
    p = _new_parser()
    try:
        p.read(CONFIG, encoding="utf-8")
    except (configparser.Error, OSError, ValueError):
        pass       # ValueError deckt UnicodeDecodeError ab
    return p


# Die Getter der Einstellungen werfen nie: eine von Hand falsch getippte Zahl
# ("abc", "0,5 s") oder ein unsinniger Wahrheitswert darf den Daemon nicht am
# Start hindern (systemd startete ihn sonst in einer Schleife neu).
def _getbool(p, section, key, fallback):
    try:
        return p.getboolean(section, key, fallback=fallback)
    except ValueError:
        return fallback


def _getint(p, section, key, fallback):
    try:
        return p.getint(section, key, fallback=fallback)
    except ValueError:
        return fallback


def _getfloat(p, section, key, fallback, lo, hi, clamp=False):
    """Zahl aus config.ini. Ein Dezimalkomma ("0,5") wird als Punkt gelesen.
    Nicht lesbar oder NaN/unendlich -> fallback. Ausserhalb von [lo, hi] ->
    fallback, oder bei clamp=True der naechste Randwert."""
    raw = p.get(section, key, fallback=None)
    if raw is None:
        return fallback
    raw = raw.strip()
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".", 1)
    try:
        v = float(raw)
    except ValueError:
        return fallback
    if not math.isfinite(v):
        return fallback
    if lo <= v <= hi:
        return v
    return min(max(v, lo), hi) if clamp else fallback


class Cfg:
    """Konfig-Zugriff mit automatischem Reload bei Dateiänderung."""

    def __init__(self):
        self.mtime = None
        self.reload(force=True)

    def reload(self, force=False):
        try:
            mtime = os.stat(CONFIG).st_mtime
        except OSError:
            mtime = None
        if not force and mtime == self.mtime:
            return False
        self.mtime = mtime
        p = _read_parser()
        g = p.get
        self.chord = g("hotkey", "chord", fallback="ctrl+meta")
        if self.chord not in CHORDS:
            self.chord = "ctrl+meta"
        self.hold_min = _getfloat(p, "hotkey", "hold_min", 0.5, 0.0, 10.0)
        self.double_window = _getfloat(p, "hotkey", "double_window", 0.45, 0.0, 10.0)
        self.language = g("speech", "language", fallback="auto")
        self.punctuation = _getbool(p, "speech", "punctuation", True)
        self.commands = _getbool(p, "speech", "commands", True)
        self.mic = g("speech", "mic", fallback="default")
        self.pill_enabled = _getbool(p, "pill", "enabled", True)
        self.pill_scale = _getfloat(p, "pill", "scale", 1.0, 0.6, 2.0, clamp=True)
        self.pill_opacity = _getfloat(p, "pill", "opacity", 0.85, 0.15, 1.0, clamp=True)
        self.pill_preview = _getbool(p, "pill", "show_preview", True)
        # Freies Verschieben per Ziehen; Position -1/-1 = automatisch (unten mittig)
        self.pill_movable = _getbool(p, "pill", "movable", False)
        # Linksklick auf die Pille öffnet das Kontrollzentrum (Standard an)
        self.pill_click_opens_center = _getbool(p, "pill", "click_opens_center", True)
        self.pill_pos_x = _getint(p, "pill", "pos_x", -1)
        self.pill_pos_y = _getint(p, "pill", "pos_y", -1)
        self.history_enabled = _getbool(p, "history", "enabled", True)
        self.ui_language = g("ui", "language", fallback="auto")
        # Streaming-Tippen (nur Freihand-Modus; Standard aus = heutiges Verhalten)
        self.streaming = _getbool(p, "streaming", "enabled", False)
        # Standard "word": Wort für Wort tippen (statt Wortblöcke)
        self.streaming_mode = g("streaming", "mode", fallback="word")
        if self.streaming_mode not in ("word", "stable", "aggressive"):
            self.streaming_mode = "word"
        # Beim Diktieren Audio leise machen: off | music | all (Standard aus)
        self.mute_mode = g("behavior", "mute_while_dictating", fallback="off")
        if self.mute_mode not in ("off", "music", "all"):
            self.mute_mode = "off"
        # Textersetzung / Snippets anwenden (Regeln in replacements.txt)
        self.text_replace = _getbool(p, "behavior", "text_replace", True)
        # Lokale Nutzungsstatistik sammeln (nur auf diesem PC)
        self.stats_enabled = _getbool(p, "behavior", "stats_enabled", True)
        # Kurzer Ton bei Aufnahme-Start/-Ende (Standard an)
        self.beep = _getbool(p, "behavior", "beep", True)
        # Ausgabegeraet fuer die Signaltoene: "system" = dem System-Standard
        # folgen, sonst der gespeicherte Geraetename (nur auf macOS waehlbar)
        self.beep_output = g("behavior", "beep_output", fallback="system")
        # Programmier-Diktat: gesprochene Symbole/Bezeichner in Code wandeln
        self.programmer_mode = _getbool(p, "speech", "programmer_mode", False)
        # Auffällige Fachwörter/Namen automatisch ins Wörterbuch lernen
        self.auto_learn = _getbool(p, "speech", "auto_learn", False)
        # Wake-Word (Freisprechen): standardmäßig AUS
        self.wakeword_enabled = _getbool(p, "wakeword", "enabled", False)
        self.wakeword_phrase = g("wakeword", "phrase", fallback=WAKEWORD_DEFAULT).strip() \
            or WAKEWORD_DEFAULT
        # Beim Start auf neue Version prüfen (Standard AUS)
        self.update_check = _getbool(p, "system", "update_check", False)
        # Erst-Einrichtung schon gesehen?
        self.onboarded = _getbool(p, "system", "onboarded", False)
        # Lokale KI-Nachbearbeitung (Ollama, opt-in, Standard AUS — bleibt lokal)
        self.ai_enabled = _getbool(p, "ai", "enabled", False)
        self.ai_endpoint = g("ai", "endpoint", fallback=OLLAMA_DEFAULT).strip() or OLLAMA_DEFAULT
        self.ai_model = g("ai", "model", fallback="").strip()
        self.ai_post_process = _getbool(p, "ai", "post_process", False)
        self.ai_post_mode = g("ai", "post_mode", fallback="cleanup").strip() or "cleanup"
        self.ai_voice_modes = _getbool(p, "ai", "voice_modes", True)
        # großzügiger Default: größere Modelle brauchen beim ersten Aufruf
        # (Kaltstart/Laden in den Speicher) gut 20-30 s.
        self.ai_timeout = _getfloat(p, "ai", "timeout", 60.0, 1.0, 3600.0)
        return True


def save(values):
    """values: dict {(section, key): value} — schreibt config.ini komplett neu."""
    p = _read_parser()
    for (sec, key), val in values.items():
        if not p.has_section(sec):
            p.add_section(sec)
        p.set(sec, key, str(val))
    buf = io.StringIO()
    p.write(buf)
    fsutil.atomic_write(CONFIG, buf.getvalue())


def reset_defaults():
    """Alle Einstellungen auf Standard zurücksetzen (config.ini entfernen).
    Wörterbuch, Textersetzungen, Verlauf und das Whisper-Modell bleiben erhalten."""
    try:
        os.remove(CONFIG)
    except OSError:
        pass


# ----------------------------------------------------------------- server.env
def read_serverenv():
    env = {}
    try:
        with open(SERVERENV, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def write_serverenv(env):
    """Atomar schreiben (siehe fsutil.atomic_write).

    Ein einfaches open(..., "w") kürzt die Datei sofort auf null und füllt sie
    erst danach. Stirbt der Prozess in diesem Fenster — oder geht der Strom
    weg —, bleibt eine leere oder halbe server.env zurück, und der nächste
    Start sucht sich Modell und Binary neu zusammen: die getroffene Modellwahl
    wäre still weg. Sichtbar wird immer entweder die alte oder die vollständige
    neue Datei."""
    fsutil.atomic_write(SERVERENV, "".join(f"{k}={v}\n" for k, v in env.items()))


# ----------------------------------------------------------------- Wörterbuch
# mtime+size als Schlüssel statt jedes Teiltranskript neu von der Platte zu
# lesen. dictionary_save() wirft den Schlüssel trotzdem weg (nicht nur auf die
# mtime-Prüfung verlassen) — ein Schreiben derselben Länge innerhalb derselben
# Dateisystem-Auflösung der mtime könnte sonst als "unverändert" durchgehen.
_dictionary_cache = {"key": None, "words": []}


def dictionary_words():
    try:
        st = os.stat(DICTIONARY)
    except OSError:
        _dictionary_cache["key"] = None
        _dictionary_cache["words"] = []
        return []
    key = (DICTIONARY, st.st_mtime, st.st_size)
    if _dictionary_cache["key"] != key:
        try:
            with open(DICTIONARY, encoding="utf-8") as f:
                _dictionary_cache["words"] = [w.strip() for w in f if w.strip()]
        except OSError:
            _dictionary_cache["words"] = []
        _dictionary_cache["key"] = key
    # Kopie zurückgeben: heutige Aufrufer verändern die Liste nicht, aber ein
    # künftiger, der sie doch verändert (z.B. sort() oder append()), würde
    # sonst den Cache für alle Leser verfälschen.
    return list(_dictionary_cache["words"])


def dictionary_save(text):
    fsutil.atomic_write(DICTIONARY, text.strip() + "\n" if text.strip() else "")
    _dictionary_cache["key"] = None


_dictionary_lock = threading.Lock()


def dictionary_learn(text, cap=500):
    """Lernt Jargon aus einem Diktat ins Wörterbuch und gibt die neu
    aufgenommenen Wörter zurück (leer, wenn nichts Neues dabei war).

    Liest die Datei dazu FRISCH von der Platte (nicht aus dem Cache und nicht
    aus einer Kopie, die der Aufrufer schon länger hält), führt zusammen und
    schreibt atomar. Was der Nutzer inzwischen im Kontrollzentrum ergänzt
    hat, geht so nicht verloren. Die Sperre schützt vor zwei Lernern im selben
    Prozess; über Prozessgrenzen bleibt nur das Fenster zwischen Lesen und
    Ersetzen, das nach Millisekunden zählt."""
    from . import learn      # erst hier: learn kennt config nicht
    with _dictionary_lock:
        _dictionary_cache["key"] = None
        merged, added = learn.learn(text, dictionary_words(), cap)
        if added:
            dictionary_save("\n".join(merged))
    return added


# ------------------------------------------------------------- Textersetzungen
def replacement_text():
    """Roher Inhalt der replacements.txt (eine Regel 'trigger=ersatz' pro Zeile)."""
    try:
        with open(REPLACEMENTS, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def replacement_save(text):
    fsutil.atomic_write(REPLACEMENTS, text.strip() + "\n" if text.strip() else "")


def replacement_rules():
    """Geparste Regeln als Liste von (trigger, ersatz) — leer bei Fehler."""
    from . import textreplace
    return textreplace.parse_rules(replacement_text())


# ------------------------------------------------------ KI-Modi (eigene Prompts)
def ai_modes_text():
    """Roher Inhalt der ai_modes.txt (eine Regel 'name=system-prompt' pro Zeile)."""
    try:
        with open(AI_MODES, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def ai_modes_save(text):
    fsutil.atomic_write(AI_MODES, text.strip() + "\n" if text.strip() else "")


# ----------------------------------------------------------------- Verlauf
def history_append(text):
    entries = history_read()
    entries.append({"text": text, "ts": __import__("time").time()})
    entries = entries[-HISTORY_MAX:]
    # 0600: die letzten Diktate sind oft Privates und sollen nur der Nutzer lesen.
    # Eine bestehende, weiter geöffnete Datei zieht beim nächsten Diktat nach.
    fsutil.atomic_write(
        HISTORY, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
        mode=0o600)


def history_read():
    out = []
    try:
        with open(HISTORY, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def history_clear():
    try:
        os.remove(HISTORY)
    except OSError:
        pass
