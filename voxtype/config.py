"""Konfiguration, Wörterbuch, Verlauf und Server-Umgebung von VoxType.

Dateien:
  ~/.config/voxtype/config.ini      Einstellungen (hotkey/speech/pill/ui)
  ~/.config/voxtype/server.env      SERVER_BIN + MODEL_PATH für voxtype-server
  ~/.config/voxtype/dictionary.txt  ein Wort pro Zeile (Erkennungs-Bias)
  ~/.local/share/voxtype/history.jsonl  letzte Diktate (lokal, abschaltbar)
"""
import configparser
import json
import os

if os.name == "nt":
    CONFDIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "VoxType")
    DATADIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VoxType")
else:
    CONFDIR = os.path.join(os.environ.get("XDG_CONFIG_HOME",
                                          os.path.expanduser("~/.config")), "voxtype")
    DATADIR = os.path.join(os.environ.get("XDG_DATA_HOME",
                                          os.path.expanduser("~/.local/share")), "voxtype")
CONFIG = os.path.join(CONFDIR, "config.ini")
SERVERENV = os.path.join(CONFDIR, "server.env")
DICTIONARY = os.path.join(CONFDIR, "dictionary.txt")
HISTORY = os.path.join(DATADIR, "history.jsonl")
HISTORY_MAX = 50

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
        self.history_enabled = p.getboolean("history", "enabled", fallback=True)
        self.ui_language = g("ui", "language", fallback="auto")
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
