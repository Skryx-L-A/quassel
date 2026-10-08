"""Per-App-Profile (Audit G102, Issue #18).

Je Anwendung andere Diktat-Einstellungen: im Terminal kein Enter am Ende und
Programmiermodus, im Mail-Programm Prosa, in der IDE etwa Englisch.

Die Profile stehen in einer eigenen Datei neben config.ini:

  <Konfigurationsverzeichnis>/profiles.ini

Gibt es diese Datei nicht, ändert sich nichts — auch die aktive Anwendung
wird dann gar nicht erst abgefragt. Die mitgelieferten Vorgaben (DEFAULTS)
legt write_defaults() einmalig als Datei an; sie gelten erst ab dann.

Aufbau, je Profil ein Abschnitt, das erste passende gewinnt:

  [profile:Terminal]
  match = com.apple.terminal, *terminal*, kitty, wt.exe
  programmer_mode = on
  allow_enter = off

  match              Bundle-ID, Prozessname oder Fensterklasse, mit * und ?,
                     Groß-/Kleinschreibung egal, mehrere durch Komma
  language           auto | de | en | … (Spracherkennung und Satzzeichen)
  ai                 on | off (lokale KI-Nachbearbeitung)
  ai_mode            Name eines KI-Modus (cleanup, email, …) für jedes Diktat;
                     off = kein Auto-Modus, Sprach-Modi bleiben
  programmer_mode    on | off
  punctuation        on | off (gesprochene Satzzeichen „Komma", „Punkt")
  allow_enter        on | off; off = Terminal-Schutz: kein „drück Enter",
                     kein Zeilenumbruch am Ende des eingefügten Texts
  enabled            off schaltet ein Profil ab, ohne es zu löschen

Was ein Profil nicht nennt, kommt aus config.ini. Ein Profil ändert die
gespeicherte Konfiguration nie: overlay() legt eine Schicht darüber, die nur
für dieses eine Diktat gilt.
"""
import configparser
import fnmatch
import os
import threading
from dataclasses import dataclass, field

from . import config, fsutil

PROFILES = os.path.join(config.CONFDIR, "profiles.ini")
SECTION_PREFIX = "profile:"

_TRUE = {"1", "on", "yes", "true", "an", "ja"}
_FALSE = {"0", "off", "no", "false", "aus", "nein"}

# Mitgelieferte Vorgaben. Bundle-IDs (macOS), Prozessnamen (Windows: .exe,
# Linux: /proc/<pid>/comm) und Fensterklassen (X11/Wayland) stehen gemischt;
# eine Kennung reicht für einen Treffer.
DEFAULTS = """\
# Quassel: Per-App-Profile. Das erste passende Profil gilt.
# Schlüssel: match, language, ai, ai_mode, programmer_mode, punctuation,
# allow_enter, enabled. Fehlt ein Schlüssel, gilt die normale Einstellung.

[profile:Terminal]
match = com.apple.terminal, com.googlecode.iterm2, com.mitchellh.ghostty,
    net.kovidgoyal.kitty, io.alacritty, dev.warp.warp-stable, org.wezfurlong.wezterm,
    *terminal*, kitty, alacritty, foot, footclient, wezterm*, ghostty, konsole,
    xterm, urxvt, tilix, terminator, st, st-256color,
    windowsterminal.exe, wt.exe, cmd.exe, powershell.exe, pwsh.exe, conhost.exe,
    mintty.exe, cascadia_hosting_window_class, consolewindowclass
allow_enter = off
programmer_mode = on

[profile:IDE]
match = com.microsoft.vscode*, com.vscodium*, com.todesktop.*, dev.zed.zed*,
    com.jetbrains.*, com.apple.dt.xcode, com.sublimetext.*,
    code, code-oss, codium, vscodium, cursor, zed, zeditor, jetbrains-*,
    sublime_text, code.exe, cursor.exe, idea64.exe, pycharm64.exe, devenv.exe,
    sublime_text.exe
programmer_mode = on

[profile:Mail]
match = com.apple.mail, com.microsoft.outlook, org.mozilla.thunderbird,
    com.readdle.smartemail-mac, thunderbird*, evolution, geary, kmail,
    org.gnome.evolution, org.gnome.geary, outlook.exe, olk.exe, thunderbird.exe
programmer_mode = off
punctuation = on
# Jedes Diktat als fertige E-Mail umschreiben (nur mit eingeschalteter KI):
# ai_mode = email
"""


@dataclass(frozen=True)
class Profile:
    name: str
    patterns: tuple
    settings: dict = field(default_factory=dict)

    def matches(self, app):
        ids = app.identifiers() if app is not None else ()
        return any(fnmatch.fnmatchcase(i, p) for p in self.patterns for i in ids)

    @property
    def allow_enter(self):
        return self.settings.get("allow_enter", True)


def _bool(value):
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def parse(text):
    """INI-Text -> Liste von Profilen in Dateireihenfolge. Unbekannte Schlüssel
    und ungültige Werte werden übergangen, nie ein Absturz."""
    p = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        p.read_string(text)
    except configparser.Error:
        return []
    out = []
    for section in p.sections():
        if not section.lower().startswith(SECTION_PREFIX):
            continue
        sec = p[section]
        if _bool(sec.get("enabled", "on")) is False:
            continue
        patterns = tuple(x.strip().lower() for x in
                         sec.get("match", "").replace("\n", ",").split(",")
                         if x.strip())
        if not patterns:
            continue
        settings = {}
        lang = sec.get("language", "").strip().lower()
        if lang and (lang == "auto" or (lang.isalpha() and 2 <= len(lang) <= 3)):
            settings["language"] = lang
        for key in ("ai", "programmer_mode", "punctuation", "allow_enter"):
            if key in sec:
                b = _bool(sec[key])
                if b is not None:
                    settings[key] = b
        mode = sec.get("ai_mode", "").strip()
        if mode:
            settings["ai_mode"] = None if _bool(mode) is False else mode
        out.append(Profile(section[len(SECTION_PREFIX):].strip(), patterns, settings))
    return out


_cache_lock = threading.Lock()
_cache = {"key": None, "profiles": []}


def load(path=None):
    """Profile aus der Datei; [] wenn sie fehlt. Liest nur bei Änderung neu."""
    path = path or PROFILES
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (path, st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if _cache["key"] == key:
            return _cache["profiles"]
    try:
        with open(path, encoding="utf-8") as f:
            profiles = parse(f.read())
    except (OSError, UnicodeDecodeError):
        profiles = []
    with _cache_lock:
        _cache["key"], _cache["profiles"] = key, profiles
    return profiles


def exists(path=None):
    return os.path.isfile(path or PROFILES)


def find(app, profiles):
    """Erstes Profil, das zur Anwendung passt, sonst None."""
    if app is None or not app.known:
        return None
    for prof in profiles:
        if prof.matches(app):
            return prof
    return None


def write_defaults(path=None):
    """Mitgelieferte Vorgaben als Datei anlegen (atomar). Eine vorhandene
    Datei bleibt unangetastet. -> True, wenn neu geschrieben."""
    path = path or PROFILES
    if os.path.exists(path):
        return False
    fsutil.atomic_write(path, DEFAULTS)
    return True


class ProfiledCfg:
    """Schicht über einer Cfg: liest alles von der Basis, außer den Werten,
    die das Profil setzt. Zuweisungen bleiben in der Schicht, die gespeicherte
    Konfiguration und das Basis-Objekt ändern sich nie."""

    def __init__(self, base, profile, overrides):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "_over", dict(overrides))

    def __getattr__(self, name):
        over = object.__getattribute__(self, "_over")
        if name in over:
            return over[name]
        return getattr(object.__getattribute__(self, "_base"), name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, "_over")[name] = value

    @property
    def allow_enter(self):
        return self.profile.allow_enter


def overrides_for(profile):
    """Profil-Einstellungen -> Cfg-Attribute."""
    s = profile.settings
    over = {}
    if "language" in s:
        over["language"] = s["language"]
    if "programmer_mode" in s:
        over["programmer_mode"] = s["programmer_mode"]
    if "punctuation" in s:
        over["punctuation"] = s["punctuation"]
    if "ai" in s:
        over["ai_enabled"] = s["ai"]
    if "ai_mode" in s:
        if s["ai_mode"] is None:
            over["ai_post_process"] = False
        else:
            over["ai_post_process"] = True
            over["ai_post_mode"] = s["ai_mode"]
    return over


def overlay(cfg, profile):
    """Cfg mit angewendetem Profil; ohne Profil die Basis selbst."""
    if profile is None:
        return cfg
    return ProfiledCfg(cfg, profile, overrides_for(profile))


def for_active_app(cfg, active_app=None, path=None, log=None):
    """Profil der aktiven Anwendung auf cfg legen. Ohne Profil-Datei wird die
    aktive Anwendung nicht abgefragt und cfg unverändert zurückgegeben.
    Geloggt wird nur Profilname und Kennung, nie ein Fenstertitel."""
    profiles = load(path)
    if not profiles:
        return cfg
    if active_app is None:
        from . import appctx
        active_app = appctx.active_app
    try:
        app = active_app()
    except Exception:
        return cfg
    prof = find(app, profiles)
    if prof is not None and log is not None:
        log("profile: %s (%s)" % (prof.name, app.label()))
    return overlay(cfg, prof)


def allow_enter(cfg):
    """False, wenn das Profil Enter verbietet (Terminal-Schutz)."""
    return getattr(cfg, "allow_enter", True) is not False


def strip_trailing_enter(text):
    """Zeilenumbrüche am Textende entfernen: im Terminal würde der letzte
    Umbruch den Befehl sofort ausführen. Andere Leerzeichen am Ende bleiben."""
    stripped = text.rstrip()
    tail = text[len(stripped):]
    return stripped if ("\n" in tail or "\r" in tail) else text
