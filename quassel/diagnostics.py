"""Diagnosebericht für Fehlermeldungen („Diagnose kopieren“ im Kontrollzentrum,
`quassel-type diagnostics`).

Ein Klartextbericht mit dem, was man zum Eingrenzen eines Fehlers braucht:
Version, Plattform, Profil und Port, Modell (nur Dateiname), Einstellungen aus
config.ini, server.env, ob der Server antwortet, die Namen der Per-App-Profile
und die letzten Zeilen des Daemon-Logs.

Nie enthalten: Verlauf, Wörterbuch, Ersetzungen, KI-Modi-Texte, Zwischenablage,
diktierter Text. Diese Dateien werden gar nicht erst geöffnet. Das Log enthält
by design keinen diktierten Text; Zeilen, die trotzdem danach aussehen
(`text=`, `hyp=`, Inhalte in Anführungszeichen über 20 Zeichen), fliegen
trotzdem heraus. Werte von Schlüsseln mit token, key, secret, password,
REQUEST_PATH werden geschwärzt, das Home-Verzeichnis wird zu `~`.

Jeder Teil wird für sich gesammelt: scheitert einer, steht im Bericht an seiner
Stelle der Grund, und der Rest kommt trotzdem.
"""
import configparser
import datetime as dt
import os
import platform as pyplatform
import re
import shutil
import socket
import subprocess
import sys

from . import __version__, config, ports, profile

REDACTED = "<entfernt>"
LINE_REMOVED = "<entfernt: Zeile könnte diktierten Text enthalten>"
LOG_LINES = 200
JOURNAL_TIMEOUT = 5.0
SERVER_TIMEOUT = 1.0
MAX_LOG_BYTES = 256 * 1024

# Schlüsselnamen, deren Werte nie im Bericht stehen.
SECRET_KEY = re.compile(r"token|key|secret|passw|request_path|auth|credential|cookie",
                        re.IGNORECASE)
# Schlüssel=Wert bzw. Schlüssel: Wert in Logzeilen mit geheimem Schlüsselnamen,
# auch in JSON/Python-Form ("api_key": "…", 'password': '…') und ohne Trenner vor
# dem geheimen Wort (apikey). Wörter wie „hotkey“ stehen in _NOT_SECRET.
_SECRET_WORDS = (r"tokens?|keys?|secrets?|passw(?:or)?d|passphrase|pwd|sig|signature"
                 r"|request_path|auth(?:orization)?|credentials?|cookie")
_SECRET_PAIR = re.compile(
    r"(?P<k>[\"']?\b[\w.]*?(?:" + _SECRET_WORDS + r")\b[\"']?)(?P<sep>\s*[=:]\s*)"
    r"(?P<v>\"[^\"]*\"|'[^']*'|[^\s&,;}\]]+)",
    re.IGNORECASE)
# Kopfzeilen ohne Doppelpunkt: „X-Api-Key S3CR3T“, „Authorization abc“.
_SECRET_HEADER = re.compile(
    r"(?P<k>\b(?:[\w]+-)+(?:" + _SECRET_WORDS + r")\b|\bauthorization\b)(?P<sep>\s+)"
    r"(?P<v>[^\s:=]+)", re.IGNORECASE)
_NOT_SECRET = re.compile(r"^[\"']?(?:hotkey|monkey|turkey|hotkeys)[\"']?$", re.IGNORECASE)
# Jeder Wert in einer URL-Abfrage (?sig=…&x=…): Signaturen heißen nicht immer „key“.
_QUERY_VALUE = re.compile(r"(?P<k>[?&][^=&\s#]+=)[^&\s#\"']+")
# URL-Zugangsdaten (http://nutzer:pass@host).
_URL_USERINFO = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^/\s@]+@", re.IGNORECASE)
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
# Logzeilen, die nach diktiertem Text aussehen könnten.
_TEXTY_KEY = re.compile(
    r"\b(text|hyp|hypothesis|transcript|raw|prompt|clipboard|partial|final_text)\s*[=:]",
    re.IGNORECASE)
_QUOTED = re.compile(r"[\"'„“”‚‘’«»]([^\"'„“”‚‘’«»\n]{21,})[\"'„“”‚‘’«»]")
# Pfad oder Bezeichner ohne Leerraum: kein Satz, darf stehen bleiben.
_PATHLIKE = re.compile(r"^[^\s]*[/\\][^\s]*$")

CONTAINS = ("version, platform, profile and port, model file name, settings from config.ini, "
            "server.env without secrets, whether the server answers, per-app profile names, "
            "the last %d lines of the daemon log" % LOG_LINES)
NEVER = ("history, dictionary, replacements, AI mode texts, clipboard, dictated text")


# ------------------------------------------------------------- Schwärzen
def _home_variants():
    home = os.path.expanduser("~")
    if not home or home in ("/", "\\") or home == "~":
        return []
    return sorted({home, home.replace("\\", "/"), home.replace("/", "\\")},
                  key=len, reverse=True)


def anonymize_paths(text):
    """Home-Verzeichnis (und damit den Nutzernamen in Pfaden) durch ~ ersetzen."""
    for variant in _home_variants():
        text = re.sub(re.escape(variant), "~", text,
                      flags=re.IGNORECASE if os.name == "nt" else 0)
    return text


def redact_value(key, value):
    if SECRET_KEY.search(key or ""):
        return REDACTED if value else value
    return redact_line(value)           # etwa ?api_key=… in einer Endpunkt-URL


def _redact_url(text):
    text = _URL_USERINFO.sub(lambda m: m.group("scheme") + REDACTED + "@", text)
    return _BEARER.sub(lambda m: m.group(1) + " " + REDACTED, text)


def _pair(match):
    if _NOT_SECRET.match(match.group("k")):
        return match.group(0)
    return match.group("k") + match.group("sep") + REDACTED


def redact_line(line):
    """Geheimnisse in einer freien Textzeile schwärzen."""
    line = _redact_url(line)            # zuerst: „Authorization: Bearer x“
    line = _QUERY_VALUE.sub(lambda m: m.group("k") + REDACTED, line)
    line = _SECRET_PAIR.sub(_pair, line)
    line = _SECRET_HEADER.sub(_pair, line)
    return anonymize_paths(line)


def looks_like_text(line):
    """True, wenn die Zeile diktierten Text enthalten könnte."""
    if _TEXTY_KEY.search(line):
        return True
    for m in _QUOTED.finditer(line):
        if not _PATHLIKE.match(m.group(1)):
            return True
    return False


def filter_log(lines):
    """Logzeilen schwärzen; verdächtige Zeilen ersetzen. -> (zeilen, entfernt)"""
    out, removed = [], 0
    for line in lines:
        line = line.rstrip("\r\n")
        if looks_like_text(line):
            out.append(LINE_REMOVED)
            removed += 1
        else:
            out.append(redact_line(line))
    return out, removed


# ------------------------------------------------------------- Teile
def _model_file(path):
    return os.path.basename(path or "") or "(none)"


def part_version():
    frozen = getattr(sys, "frozen", False)
    return [
        "quassel: %s" % __version__,
        "platform: %s" % pyplatform.platform(),
        "machine: %s" % pyplatform.machine(),
        "python: %s (%s)" % (pyplatform.python_version(), pyplatform.python_implementation()),
        "build: %s" % ("app bundle" if frozen else "source"),
        "session: %s" % (os.environ.get("XDG_SESSION_TYPE") or "-"),
        "desktop: %s" % (os.environ.get("XDG_CURRENT_DESKTOP") or "-"),
    ]


def part_instance():
    return [
        "profile: %s" % (profile.name() or "default"),
        *part_port(),
        "config dir: %s" % config.CONFDIR,
        "data dir: %s" % config.DATADIR,
    ]


def part_port():
    """Wirksamer Server-Port; wich Quassel aus, auch der Vorgabeport."""
    port, base = ports.current(), ports.default()
    lines = ["port: %d" % port]
    if port != base:
        lines.append("port note: default port %d was in use, chosen automatically" % base)
    return lines


def part_model():
    env = config.read_serverenv()
    lines = ["model: %s" % _model_file(env.get("MODEL_PATH"))]
    if env.get("VAD_MODEL"):
        lines.append("vad model: %s" % _model_file(env.get("VAD_MODEL")))
    path = env.get("MODEL_PATH")
    if path:
        lines.append("model present: %s" % ("yes" if os.path.isfile(path) else "no"))
    return lines


def part_settings():
    if not os.path.isfile(config.CONFIG):
        return ["(no config.ini: all settings are defaults)"]
    p = configparser.ConfigParser(interpolation=None)
    with open(config.CONFIG, encoding="utf-8") as f:
        p.read_file(f)
    lines = []
    for section in p.sections():
        lines.append("[%s]" % section)
        for key, value in p.items(section):
            lines.append("%s = %s" % (key, redact_value(key, value)))
    return lines or ["(config.ini is empty)"]


def part_serverenv():
    env = config.read_serverenv()
    if not env:
        return ["(no server.env)"]
    return ["%s=%s" % (k, redact_value(k, v)) for k, v in env.items()]


def part_server():
    from . import whisperclient
    up = whisperclient.server_up(timeout=SERVER_TIMEOUT)
    return ["reachable: %s" % ("yes" if up else "no"),
            "address: 127.0.0.1:%d" % ports.current()]


def part_profiles():
    from . import profiles
    path = profiles.PROFILES
    if not os.path.isfile(path):
        return ["(no profiles.ini)"]
    p = configparser.ConfigParser(interpolation=None)
    with open(path, encoding="utf-8") as f:
        p.read_file(f)
    names = [s[len(profiles.SECTION_PREFIX):] for s in p.sections()
             if s.startswith(profiles.SECTION_PREFIX)]
    return ["profiles: %d" % len(names)] + ["- %s" % n for n in names]


def daemon_log_file():
    """macOS ~/Library/Logs/<Profil>/daemon.log, Windows debug.log im Datenordner.
    Linux: None (journald)."""
    if sys.platform == "darwin":
        return os.path.expanduser(os.path.join("~/Library/Logs", profile.dirname("Quassel"),
                                               "daemon.log"))
    if os.name == "nt":
        return os.path.join(config.DATADIR, "debug.log")
    return None


def tail_file(path, n=LOG_LINES):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - MAX_LOG_BYTES))
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    if size > MAX_LOG_BYTES and lines:
        lines = lines[1:]           # erste Zeile ist angeschnitten
    return lines[-n:]


def journal_lines(n=LOG_LINES, timeout=JOURNAL_TIMEOUT):
    exe = shutil.which("journalctl")
    if not exe:
        raise FileNotFoundError("journalctl not found")
    unit = profile.service("quasseld")
    r = subprocess.run([exe, "--user", "-u", unit, "-n", str(n), "--no-pager",
                        "-o", "short-iso"],
                       capture_output=True, text=True, timeout=timeout, check=False,
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError("journalctl exited with %d" % r.returncode)
    return r.stdout.splitlines()[-n:]


def part_log():
    path = daemon_log_file()
    if path is None:
        source = "journalctl --user -u %s" % profile.service("quasseld")
        raw = journal_lines()
    elif os.path.isfile(path):
        source = path
        raw = tail_file(path)
    else:
        return ["source: %s" % path, "(no log file yet)"]
    lines, removed = filter_log(raw)
    host = socket.gethostname().split(".")[0]
    if len(host) >= 3:
        lines = [re.sub(r"\b%s\b" % re.escape(host), "<host>", line) for line in lines]
    return (["source: %s" % source,
             "lines: %d, removed as possibly dictated text: %d" % (len(lines), removed),
             ""] + lines)


PARTS = (
    ("Version", part_version),
    ("Instance", part_instance),
    ("Model", part_model),
    ("Settings (config.ini)", part_settings),
    ("Server environment (server.env)", part_serverenv),
    ("Server", part_server),
    ("Per-app profiles (profiles.ini, names only)", part_profiles),
    ("Daemon log (last %d lines)" % LOG_LINES, part_log),
)


def _secret_values():
    """Werte geheimer server.env-Schlüssel: falls sie anderswo (Log, URL)
    auftauchen, werden sie dort ebenfalls geschwärzt."""
    values = set()
    try:
        env = config.read_serverenv()
        values |= {v.strip() for k, v in env.items() if SECRET_KEY.search(k)}
    except Exception:  # noqa: BLE001
        pass
    try:                                # geheim benannte Werte aus config.ini ebenso
        parser = config._read_parser()
        for section in parser.sections():
            values |= {v.strip() for k, v in parser.items(section) if SECRET_KEY.search(k)}
    except Exception:  # noqa: BLE001
        pass
    return sorted((v for v in values if len(v) >= 4), key=len, reverse=True)


def _reason(exc):
    """Grund ohne Dateiinhalt: configparser zitiert in seinen Meldungen die
    kaputte Zeile, und die könnte ein Geheimnis sein."""
    name = type(exc).__name__
    if isinstance(exc, configparser.Error):
        return name
    msg = redact_line(str(exc))[:200]
    return "%s: %s" % (name, msg) if msg else name


def build_report(parts=PARTS, now=None):
    """Den ganzen Bericht als Text. Wirft nie: ein Teil, der scheitert, steht
    mit Grund im Bericht."""
    now = now or dt.datetime.now().astimezone()
    out = [
        "Quassel diagnostics",
        "generated: %s" % now.isoformat(timespec="seconds"),
        "contains: %s" % CONTAINS,
        "never contains: %s" % NEVER,
    ]
    for title, fn in parts:
        out += ["", "== %s ==" % title]
        try:
            out += [str(line) for line in fn()]
        except subprocess.TimeoutExpired:
            out.append("<not collected: timed out>")
        except Exception as exc:  # noqa: BLE001 — ein Teil darf den Bericht nicht verhindern
            out.append("<not collected: %s>" % _reason(exc))
    text = "\n".join(out) + "\n"
    for secret in _secret_values():
        text = text.replace(secret, REDACTED)
    return anonymize_paths(text)


def default_filename(now=None):
    now = now or dt.datetime.now()
    return "quassel-diagnostics-%s.txt" % now.strftime("%Y%m%d-%H%M%S")


def save_report(path, text):
    """Atomar und nur für den Nutzer lesbar (0600)."""
    from . import fsutil
    fsutil.atomic_write(path, text, mode=0o600)
