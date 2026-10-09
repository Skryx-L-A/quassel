"""Konfiguration, Wörterbuch, Verlauf und Server-Umgebung von Quassel.

Dateien:
  ~/.config/quassel/config.ini      Einstellungen (hotkey/speech/pill/ui)
  ~/.config/quassel/server.env      SERVER_BIN + MODEL_PATH für quassel-server
  ~/.config/quassel/dictionary.txt  ein Wort pro Zeile (Erkennungs-Bias)
  ~/.local/share/quassel/history.jsonl  letzte Diktate (lokal, abschaltbar)
"""
import configparser
import contextlib
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
# Automatisch gelernte Wörter mit Herkunft, getrennt vom eigenen Wörterbuch.
LEARNED = os.path.join(CONFDIR, "dictionary-learned.json")
REPLACEMENTS = os.path.join(CONFDIR, "replacements.txt")
CLEANUP_CORRECTIONS = os.path.join(CONFDIR, "cleanup-corrections.txt")
AI_MODES = os.path.join(CONFDIR, "ai_modes.txt")
HISTORY = os.path.join(DATADIR, "history.jsonl")
HISTORY_MAX = 50
CONFIG_VERSION = 1
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


def _read_parser_strict():
    """(Parser, ob config.ini vollständig gelesen wurde). Fehlt die Datei,
    gilt das als vollständig (nichts zu verlieren)."""
    p = _new_parser()
    if not os.path.exists(CONFIG):
        return p, True
    try:
        with open(CONFIG, encoding="utf-8") as f:
            p.read_file(f)
        return p, True
    except (configparser.Error, OSError, ValueError):
        return _read_parser(), False


def _keep_damaged_copy():
    """Kopie einer nicht vollständig lesbaren config.ini neben das Original,
    bevor ein Schreiben den lesbaren Teilstand an ihre Stelle setzt: die
    Einstellungen hinter der kaputten Stelle bleiben so wiederherstellbar."""
    try:
        with open(CONFIG, "rb") as f:
            data = f.read()
        backup = CONFIG + ".damaged-" + __import__("time").strftime("%Y%m%d-%H%M%S")
        # Byteweise: eine kaputte Datei kann ungültiges UTF-8 enthalten.
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return backup
    except OSError:
        return None


def _write_parser(p):
    buf = io.StringIO()
    p.write(buf)
    fsutil.atomic_write(CONFIG, buf.getvalue())


def _migration_1(p):
    """Basisformat markieren; ältere Einstellungen brauchen keine Umformung."""


MIGRATIONS = ((1, _migration_1),)


def migrate():
    """Führt jede bekannte config.ini-Migration genau einmal atomar aus.

    Alte Dateien ohne ``[meta] version`` gelten als Version 0. Unbekannte
    neuere Versionen werden nicht zurückgeschrieben.
    """
    if not os.path.exists(CONFIG):
        return False             # nichts zu migrieren; save() legt die Datei an
    p, complete = _read_parser_strict()
    if not complete:
        # Nie eine nur teilweise gelesene Datei als vollständige zurückschreiben.
        return False
    try:
        version = p.getint("meta", "version", fallback=0)
    except ValueError:
        version = 0
    if version >= CONFIG_VERSION:
        return False
    changed = False
    for target, callback in MIGRATIONS:
        if version < target:
            callback(p)
            if not p.has_section("meta"):
                p.add_section("meta")
            p.set("meta", "version", str(target))
            version = target
            changed = True
    if changed:
        _write_parser(p)
    return changed


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


def _getint_range(p, section, key, fallback, lo, hi):
    value = _getint(p, section, key, fallback)
    return value if lo <= value <= hi else fallback


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
        migrate()
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
        # „ss" -> „ß" (quassel/eszett.py): auto = nur mit einem deutschen
        # Finetune-Modell, das nie „ß" schreibt; on/off erzwingen es.
        self.eszett = g("speech", "eszett", fallback="auto").strip().lower()
        if self.eszett not in ("auto", "on", "off"):
            self.eszett = "auto"
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
        # 0 ist die ausdrücklich gewählte unbegrenzte Aufbewahrung.
        self.history_days = _getint_range(p, "history", "history_days", 7, 0, 36500)
        # Vorlaufpuffer (C47): 0 = aus. Bei > 0 bleibt das Mikrofon nach einem
        # Diktat einige Sekunden offen; macOS zeigt dann die Mikrofon-Anzeige.
        self.preroll_ms = _getint_range(p, "speech", "preroll_ms", 0, 0, 2000)
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
        # Separate, opt-in dictation cleanup; legacy AI modes stay unchanged.
        self.cleanup_enabled = _getbool(p, "cleanup", "enabled", False)
        self.cleanup_level = g("cleanup", "level", fallback="aus").strip().lower()
        if self.cleanup_level not in ("aus", "leicht", "mittel", "stark"):
            self.cleanup_level = "aus"
        # Optionaler lokaler Entscheider als zweite Prüfung (quassel/decider.py).
        # Nur eine Adresse auf diesem Rechner; alles andere bleibt aus.
        from . import decider
        url = g("cleanup", "decider_url", fallback="").strip()
        self.cleanup_decider_url = url if decider.usable(url) else ""
        self.cleanup_decider_timeout = _getfloat(p, "cleanup", "decider_timeout", 2.0, 0.1, 30.0)
        return True

    @property
    def cleanup_terms(self):
        """Current ASR dictionary words are protected during cleanup."""
        return dictionary_words()

    @property
    def cleanup_replacements(self):
        """Current explicit correction list, read only when cleanup is used."""
        return cleanup_corrections()


def save(values):
    """Ändert nur die übergebenen Schlüssel gegen den frisch gelesenen Stand.

    Damit bleibt eine zwischen Laden und Speichern erfolgte Änderung eines
    anderen Schreibers erhalten, sofern der Aufrufer nur seinen geänderten
    Schlüssel übergibt.
    """
    migrate()
    p, complete = _read_parser_strict()
    if not complete:
        _keep_damaged_copy()
    for (sec, key), val in values.items():
        if not p.has_section(sec):
            p.add_section(sec)
        p.set(sec, key, str(val))
    _write_parser(p)


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
    fsutil.atomic_write(SERVERENV, "".join(f"{k}={v}\n" for k, v in env.items()),
                        mode=0o600)


# ----------------------------------------------------------------- Wörterbuch
# mtime+size als Schlüssel statt jedes Teiltranskript neu von der Platte zu
# lesen. dictionary_save() wirft den Schlüssel trotzdem weg (nicht nur auf die
# mtime-Prüfung verlassen) — ein Schreiben derselben Länge innerhalb derselben
# Dateisystem-Auflösung der mtime könnte sonst als "unverändert" durchgehen.
_dictionary_cache = {"key": None, "words": []}


def _now():
    return __import__("time").time()


def dictionary_manual():
    """Das eigene Wörterbuch (dictionary.txt), ohne gelernte Wörter."""
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


def dictionary_words(now=None):
    """Was als Erkennungshilfe wirkt: eigene Wörter, dann gelernte, die gerade
    gelten (bestätigt, nicht verfallen, nicht gesperrt)."""
    from . import learn      # erst hier: learn kennt config nicht
    manual = dictionary_manual()
    have = {w.casefold() for w in manual}
    learned = learn.active_words(_learned_state(), _now() if now is None else now)
    return manual + [w for w in learned if w.casefold() not in have]


def dictionary_save(text):
    fsutil.atomic_write(DICTIONARY, text.strip() + "\n" if text.strip() else "")
    _dictionary_cache["key"] = None


def cleanup_corrections():
    """Read literal spoken=written pairs; never expand snippet placeholders."""
    try:
        with open(CLEANUP_CORRECTIONS, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return {}
    corrections = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        spoken, written = (part.strip() for part in line.split("=", 1))
        if spoken and written and len(spoken) <= 100 and len(written) <= 100:
            corrections[spoken] = written
    return corrections


_dictionary_lock = threading.Lock()
_learned_cache = {"key": None, "state": None}


def _learned_state():
    """Gelernte Wörter, gecacht nach mtime+size; kaputte Datei = leer."""
    from . import learn
    try:
        st = os.stat(LEARNED)
    except OSError:
        return learn.empty_state()
    key = (LEARNED, st.st_mtime_ns, st.st_size)
    if _learned_cache["key"] != key:
        _learned_cache["state"] = _read_learned()
        _learned_cache["key"] = key
    return _learned_cache["state"]


def _read_learned():
    from . import learn
    try:
        with open(LEARNED, encoding="utf-8") as f:
            return learn.normalize_state(json.load(f))
    except (OSError, ValueError):
        return learn.empty_state()


def _write_learned(state):
    # 0600: die Wörter verraten, worüber jemand diktiert.
    fsutil.atomic_write(LEARNED, json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                        mode=0o600)
    _learned_cache["key"] = None


@contextlib.contextmanager
def _learned_update():
    """Liest die gelernten Wörter frisch unter einer Sperre über alle Prozesse
    (Daemon lernt, Kontrollzentrum oder CLI entfernen) und schreibt sie nur,
    wenn sich etwas geändert hat."""
    with _dictionary_lock, _file_lock(LEARNED):
        state = _read_learned()
        before = json.dumps(state, sort_keys=True)
        yield state
        if json.dumps(state, sort_keys=True) != before:
            _write_learned(state)


def _correction_triggers():
    """Schreibweisen, die der Nutzer schon als falsch korrigiert: Auslöser in
    den Textersetzungen und den Korrekturen der Bereinigung."""
    triggers = list(cleanup_corrections())
    for line in replacement_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            trigger = line.split("=", 1)[0].strip()
            if trigger:
                triggers.append(trigger)
    return triggers


def dictionary_learn(text, now=None):
    """Zählt die Fachbegriffe eines Diktats und gibt die Wörter zurück, die
    dadurch neu wirken (leer, wenn keins). Gelernte Wörter stehen in LEARNED,
    nie in dictionary.txt; siehe learn.py zu Bestätigung und Verfall."""
    from . import learn
    now = _now() if now is None else now
    words = learn.jargon_words(text)
    with _learned_update() as state:
        added = learn.observe(state, words, now, manual=dictionary_manual(),
                              blocked=_correction_triggers()) if words else []
        learn.prune(state, now)
    return added


def dictionary_learned(now=None):
    """Gelernte Wörter, die gerade wirken, mit Herkunft (für Anzeige/CLI)."""
    from . import learn
    now = _now() if now is None else now
    state = _learned_state()
    return [dict(word=w, **{k: state["words"][w][k] for k in ("first", "last", "seen", "active")})
            for w in learn.active_words(state, now)]


def dictionary_forget(word):
    """Entfernt ein gelerntes Wort und sperrt es für immer."""
    from . import learn
    with _learned_update() as state:
        return learn.forget(state, word)


def dictionary_keep(word):
    """Übernimmt ein gelerntes Wort ins eigene Wörterbuch (dort verfällt nichts)."""
    from . import learn
    with _learned_update() as state:
        item = learn.entry(state, word)
        if item is None:
            return False
        known = next(w for w in state["words"] if w.casefold() == word.casefold())
        learn.drop(state, known)
    manual = dictionary_manual()
    if known.casefold() not in {w.casefold() for w in manual}:
        dictionary_save("\n".join(manual + [known]))
    return True


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
def _history_days(days):
    if days is None:
        days = Cfg().history_days
    try:
        days = int(days)
    except (TypeError, ValueError):
        return 7
    return days if 0 <= days <= 36500 else 7


def _write_history(entries):
    fsutil.atomic_write(
        HISTORY, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
        mode=0o600)


@contextlib.contextmanager
def _history_lock():
    """Ein Schreiber zur Zeit über alle Prozesse: Daemon (anhängen),
    Kontrollzentrum, CLI und MCP (Ablauf entfernen, leeren). Ohne Sperre
    überschrieb ein Leser, der abgelaufene Einträge wegschrieb, ein Diktat,
    das der Daemon zwischen Lesen und Ersetzen angehängt hatte."""
    with _file_lock(HISTORY):
        yield


@contextlib.contextmanager
def _file_lock(path):
    """Exklusive Sperre auf path + ".lock" über Prozessgrenzen."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)   # wartet bis zu ~10 s
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(fd)          # gibt eine flock-Sperre mit frei


def _read_history_file(cutoff):
    """(gültige Einträge, ob die Datei bereinigt werden muss) — liest nur."""
    out = []
    changed = False
    try:
        with open(HISTORY, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    ts = entry.get("ts") if isinstance(entry, dict) else None
                    if not isinstance(ts, (int, float)):
                        changed = True
                    elif cutoff is not None and ts < cutoff:
                        changed = True
                    else:
                        out.append(entry)
                except ValueError:
                    changed = True
    except OSError:
        pass
    return out, changed


def _history_cutoff(history_days):
    days = _history_days(history_days)
    return __import__("time").time() - days * 86400 if days else None


def history_append(text, *, history_days=None):
    with _history_lock():
        entries, _ = _read_history_file(_history_cutoff(history_days))
        entries.append({"text": text, "ts": __import__("time").time()})
        entries = entries[-HISTORY_MAX:]
        # 0600: die letzten Diktate sind oft Privates und sollen nur der Nutzer lesen.
        # Eine bestehende, weiter geöffnete Datei zieht beim nächsten Diktat nach.
        _write_history(entries)


def history_read(*, history_days=None):
    """Liest den Verlauf und entfernt abgelaufene Einträge atomar (unter der
    Sperre: ein gleichzeitig angehängtes Diktat geht nicht verloren)."""
    cutoff = _history_cutoff(history_days)
    out, changed = _read_history_file(cutoff)
    if changed:
        with _history_lock():
            out, changed = _read_history_file(cutoff)   # Stand unter der Sperre
            if changed:
                _write_history(out)
    return out


def history_clear():
    with _history_lock():
        try:
            os.remove(HISTORY)
        except OSError:
            pass
