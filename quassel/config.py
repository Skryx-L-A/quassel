"""Konfiguration, Wörterbuch, Verlauf und Server-Umgebung von Quassel.

Dateien:
  ~/.config/quassel/config.ini      Einstellungen (hotkey/speech/pill/ui)
  ~/.config/quassel/server.env      SERVER_BIN + MODEL_PATH für quassel-server
  ~/.config/quassel/dictionary.txt  ein Wort pro Zeile (Erkennungs-Bias)
  ~/.local/share/quassel/history.jsonl  letzte Diktate (lokal, abschaltbar)
"""
import configparser
import json
import os

if os.name == "nt":
    CONFDIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Quassel")
    DATADIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Quassel")
else:
    CONFDIR = os.path.join(os.environ.get("XDG_CONFIG_HOME",
                                          os.path.expanduser("~/.config")), "quassel")
    DATADIR = os.path.join(os.environ.get("XDG_DATA_HOME",
                                          os.path.expanduser("~/.local/share")), "quassel")
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
MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo"]
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{}.bin"


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
        p = configparser.ConfigParser()
        try:
            p.read(CONFIG)
        except (configparser.Error, OSError):
            pass
        g = p.get
        self.chord = g("hotkey", "chord", fallback="ctrl+meta")
        if self.chord not in CHORDS:
            self.chord = "ctrl+meta"
        self.hold_min = p.getfloat("hotkey", "hold_min", fallback=0.5)
        self.double_window = p.getfloat("hotkey", "double_window", fallback=0.45)
        self.language = g("speech", "language", fallback="auto")
        self.punctuation = p.getboolean("speech", "punctuation", fallback=True)
        self.commands = p.getboolean("speech", "commands", fallback=True)
        self.mic = g("speech", "mic", fallback="default")
        self.pill_enabled = p.getboolean("pill", "enabled", fallback=True)
        self.pill_scale = p.getfloat("pill", "scale", fallback=1.0)
        self.pill_opacity = p.getfloat("pill", "opacity", fallback=0.85)
        self.pill_preview = p.getboolean("pill", "show_preview", fallback=True)
        self.history_enabled = p.getboolean("history", "enabled", fallback=True)
        self.ui_language = g("ui", "language", fallback="auto")
        # Streaming-Tippen (nur Freihand-Modus; Standard aus = heutiges Verhalten)
        self.streaming = p.getboolean("streaming", "enabled", fallback=False)
        # Standard "word": Wort für Wort tippen (statt Wortblöcke)
        self.streaming_mode = g("streaming", "mode", fallback="word")
        if self.streaming_mode not in ("word", "stable", "aggressive"):
            self.streaming_mode = "word"
        # Beim Diktieren Audio leise machen: off | music | all (Standard aus)
        self.mute_mode = g("behavior", "mute_while_dictating", fallback="off")
        if self.mute_mode not in ("off", "music", "all"):
            self.mute_mode = "off"
        # Textersetzung / Snippets anwenden (Regeln in replacements.txt)
        self.text_replace = p.getboolean("behavior", "text_replace", fallback=True)
        # Lokale Nutzungsstatistik sammeln (nur auf diesem PC)
        self.stats_enabled = p.getboolean("behavior", "stats_enabled", fallback=True)
        # Programmier-Diktat: gesprochene Symbole/Bezeichner in Code wandeln
        self.programmer_mode = p.getboolean("speech", "programmer_mode", fallback=False)
        # Auffällige Fachwörter/Namen automatisch ins Wörterbuch lernen
        self.auto_learn = p.getboolean("speech", "auto_learn", fallback=False)
        # Wake-Word (Freisprechen): standardmäßig AUS
        self.wakeword_enabled = p.getboolean("wakeword", "enabled", fallback=False)
        self.wakeword_phrase = g("wakeword", "phrase", fallback=WAKEWORD_DEFAULT).strip() \
            or WAKEWORD_DEFAULT
        # Beim Start auf neue Version prüfen (Standard AUS)
        self.update_check = p.getboolean("system", "update_check", fallback=False)
        # Erst-Einrichtung schon gesehen?
        self.onboarded = p.getboolean("system", "onboarded", fallback=False)
        # Lokale KI-Nachbearbeitung (Ollama, opt-in, Standard AUS — bleibt lokal)
        self.ai_enabled = p.getboolean("ai", "enabled", fallback=False)
        self.ai_endpoint = g("ai", "endpoint", fallback=OLLAMA_DEFAULT).strip() or OLLAMA_DEFAULT
        self.ai_model = g("ai", "model", fallback="").strip()
        self.ai_post_process = p.getboolean("ai", "post_process", fallback=False)
        self.ai_post_mode = g("ai", "post_mode", fallback="cleanup").strip() or "cleanup"
        self.ai_voice_modes = p.getboolean("ai", "voice_modes", fallback=True)
        # großzügiger Default: größere Modelle brauchen beim ersten Aufruf
        # (Kaltstart/Laden in den Speicher) gut 20-30 s.
        self.ai_timeout = p.getfloat("ai", "timeout", fallback=60.0)
        return True


def save(values):
    """values: dict {(section, key): value} — schreibt config.ini komplett neu."""
    p = configparser.ConfigParser()
    p.read(CONFIG)
    for (sec, key), val in values.items():
        if not p.has_section(sec):
            p.add_section(sec)
        p.set(sec, key, str(val))
    os.makedirs(CONFDIR, exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        p.write(f)


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
    os.makedirs(CONFDIR, exist_ok=True)
    with open(SERVERENV, "w", encoding="utf-8") as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")


# ----------------------------------------------------------------- Wörterbuch
def dictionary_words():
    try:
        with open(DICTIONARY, encoding="utf-8") as f:
            return [w.strip() for w in f if w.strip()]
    except OSError:
        return []


def dictionary_save(text):
    os.makedirs(CONFDIR, exist_ok=True)
    with open(DICTIONARY, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n" if text.strip() else "")


# ------------------------------------------------------------- Textersetzungen
def replacement_text():
    """Roher Inhalt der replacements.txt (eine Regel 'trigger=ersatz' pro Zeile)."""
    try:
        with open(REPLACEMENTS, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def replacement_save(text):
    os.makedirs(CONFDIR, exist_ok=True)
    with open(REPLACEMENTS, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n" if text.strip() else "")


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
    os.makedirs(CONFDIR, exist_ok=True)
    with open(AI_MODES, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n" if text.strip() else "")


# ----------------------------------------------------------------- Verlauf
def history_append(text):
    os.makedirs(DATADIR, exist_ok=True)
    entries = history_read()
    entries.append({"text": text, "ts": __import__("time").time()})
    entries = entries[-HISTORY_MAX:]
    with open(HISTORY, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


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
