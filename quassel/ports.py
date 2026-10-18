"""Server-Port: Vorgabe, Ausweichen bei Belegung, Weitergabe an die Clients.

Der whisper-server lauscht auf dem Vorgabeport (8765, ein Profil auf seinem
eigenen, siehe profile.port()). Belegt ein fremder Dienst diesen Port, nimmt
Quassel den nächsten freien Port aus FALLBACK und legt ihn in die
Laufzeitdatei <RUNDIR>/server.port. Alle Clients lesen den Port dort, bei
fehlender oder kaputter Datei gilt der Vorgabeport. Ein fremder Prozess wird
nie beendet. Ein gesetztes QUASSEL_PORT ist fest: dann kein Ausweichen,
sondern eine klare Meldung.

Linux: die systemd-Unit ruft vor dem Start `python -m quassel.ports`
(bin/quassel-server-port) und liest den Port danach aus derselben Datei.
"""
import os
import socket
import sys
import urllib.request

from . import config, fsutil, profile, state

HOST = "127.0.0.1"
# Außerhalb der Profilports 8766..8865, damit ein ausweichender Server nie den
# festen Port eines anderen Profils besetzt.
FALLBACK = range(8870, 8890)
FILENAME = "server.port"

FREE, OURS, BUSY = "free", "ours", "busy"

# Ohne Proxy: der Server läuft auf 127.0.0.1 (siehe whisperclient._probe).
_probe = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def path():
    # state.RUNDIR zur Aufrufzeit lesen: Tests und Profile ändern es.
    return os.path.join(state.RUNDIR, FILENAME)


def default():
    """Vorgabeport dieser Instanz (8765, Profilport oder QUASSEL_PORT)."""
    return profile.port()


def fixed():
    """True, wenn QUASSEL_PORT gültig gesetzt ist: dann wird nie ausgewichen."""
    env = os.environ.get("QUASSEL_PORT", "").strip()
    return env.isdigit() and 1024 <= int(env) <= 65535


def _valid(port):
    return isinstance(port, int) and 1024 <= port <= 65535


def read_file():
    """Port aus der Laufzeitdatei oder None (fehlt, kaputt, außer Bereich)."""
    try:
        with open(path(), encoding="utf-8") as f:
            value = f.read().strip()
    except (OSError, UnicodeError):
        return None
    if not (value.isascii() and value.isdigit()):
        return None
    port = int(value)
    return port if _valid(port) else None


def current():
    """Port, unter dem die Clients den Server erreichen."""
    if fixed():
        return default()
    return read_file() or default()


def _write_file(port):
    try:
        os.makedirs(state.RUNDIR, mode=0o700, exist_ok=True)
        fsutil.atomic_write(path(), "%d\n" % port, mode=0o600)
    except OSError as e:
        print("quassel ports: %s not written: %s" % (path(), e),
              file=sys.stderr, flush=True)


def _request_path():
    """REQUEST_PATH aus server.env, geprüft wie in whisperclient.server_url()."""
    value = config.read_serverenv().get("REQUEST_PATH", "").strip()
    if value.startswith("/") and "?" not in value and "#" not in value:
        return value.rstrip("/")
    return ""


def _connects(port, timeout):
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def _bindable(port):
    """Lässt sich 127.0.0.1:port binden? Ohne SO_REUSEADDR: unter Windows
    hieße das, einen fremden Port mitzubenutzen."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _is_ours(port, timeout):
    """Antwortet dort unser whisper-server? Wie whisperclient.server_up():
    2xx auf <REQUEST_PATH>/ und der Header Server: whisper.cpp."""
    url = "http://%s:%d%s/" % (HOST, port, _request_path())
    try:
        with _probe.open(url, timeout=timeout) as response:
            server = response.headers.get("Server", "")
    except Exception:  # noqa: BLE001 — jede Ausnahme = nicht unser Server
        return False
    return server.casefold().startswith("whisper.cpp")


def status(port, timeout=0.5):
    """FREE (kein Zuhörer, bindbar), OURS (unser whisper-server antwortet)
    oder BUSY (etwas anderes hält den Port)."""
    if _connects(port, timeout):
        return OURS if _is_ours(port, max(timeout, 1.0)) else BUSY
    return FREE if _bindable(port) else BUSY


class Choice:
    """Ergebnis von choose(): port und status (FREE, OURS oder BUSY)."""

    def __init__(self, port, status, message=""):
        self.port, self.status, self.message = port, status, message

    def __repr__(self):
        return "Choice(%d, %s)" % (self.port, self.status)


def choose():
    """Port für den nächsten Serverstart wählen und in die Laufzeitdatei legen.

    FREE: dort starten. OURS: unser Server läuft schon, keinen zweiten
    starten. BUSY: kein Port frei bzw. QUASSEL_PORT belegt; nicht starten,
    message erklärt warum."""
    base = default()
    if fixed():
        found = status(base)
        _write_file(base)
        if found == BUSY:
            message = ("port %d is in use by another program; QUASSEL_PORT is set, "
                       "so Quassel does not switch to another port" % base)
            print("quassel ports: " + message, file=sys.stderr, flush=True)
            return Choice(base, BUSY, message)
        return Choice(base, found)
    previous = read_file()
    candidates = [base]
    if previous is not None and previous != base:
        candidates.append(previous)
    candidates += [p for p in FALLBACK if p not in candidates]
    for port in candidates:
        found = status(port)
        if found == BUSY:
            continue
        if previous != port:
            _write_file(port)
            if port != base:
                # Einmal je Wechsel: der nächste Aufruf findet die Datei schon.
                print("quassel ports: port %d is in use by another program; "
                      "the speech server uses port %d" % (base, port),
                      file=sys.stderr, flush=True)
        return Choice(port, found)
    message = ("port %d and the fallback ports %d-%d are all in use"
               % (base, FALLBACK[0], FALLBACK[-1]))
    print("quassel ports: " + message, file=sys.stderr, flush=True)
    return Choice(base, BUSY, message)


def main():
    """Für die systemd-Unit: Port wählen, Datei schreiben, Port ausgeben.
    Rückgabe 1 nur, wenn kein Port frei ist (die Unit startet trotzdem und
    meldet den Bindefehler wie bisher)."""
    choice = choose()
    print(choice.port)
    return 1 if choice.status == BUSY else 0


if __name__ == "__main__":
    sys.exit(main())
