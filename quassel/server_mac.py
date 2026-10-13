"""whisper-server-Verwaltung für macOS (Gegenstück zu quassel/win/server.py).

Kein systemd auf dem Mac — der Server läuft als Kindprozess der App.
Konfiguration über dieselbe server.env wie auf Linux/Windows:
  SERVER_BIN       Pfad zum whisper-server-Binary (Metal-Build)
  MODEL_PATH       ggml-Modell unter ~/Library/Application Support/Quassel/models
  WHISPER_THREADS  Threads (bis 8)
  WHISPER_DECODE   Decode-Flags ("-bs 1", gierige Suche — gemessen über 36
                   Dateien/938 Referenzwörter nie schlechter als beam_size=5,
                   ~6% schneller)
  VAD_MODEL        Silero-VAD-Modell (optional)

ensure_env() füllt fehlende Einträge mit Hardware-Defaults, überschreibt aber
nie eine schon getroffene Wahl (gleiches Prinzip wie install.sh/win-provision)
— einzige Ausnahme: der überholte WHISPER_DECODE-Altwert "-bs 5" wird einmalig
auf "-bs 1" gehoben (siehe ensure_env()).
"""
import os
import secrets
import signal
import socket
import subprocess
import sys
import io
import threading

from . import config, fsutil, hwdetect, profile, state

MODEL_DIR = config.MODELDIR
HOST, PORT = "127.0.0.1", str(profile.port())

_proc = None
_request_path_support = {}
LOG_FILE = os.path.join(config.DATADIR, "whisper-server.log")


def _log_stream(extra=0):
    os.makedirs(config.DATADIR, exist_ok=True)
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) + extra >= 1024 * 1024:
        for n in (2, 1):
            source = LOG_FILE if n == 1 else LOG_FILE + ".1"
            if os.path.exists(source):
                os.replace(source, LOG_FILE + f".{n}")
    return open(LOG_FILE, "ab")


def _drain_log(pipe):
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                break
            with _log_stream(len(chunk)) as log:
                log.write(chunk)
    finally:
        pipe.close()


def _pid_path():
    """PID-Datei des eigenen Servers im Laufzeitordner des Profils."""
    return os.path.join(state.RUNDIR, "whisper-server.pid")


def _write_pid(pid):
    """PID des eigenen Kinds ablegen (0600, atomar). Ein Fehler kostet nur
    die spätere Aufräumhilfe, nie den Start."""
    try:
        os.makedirs(state.RUNDIR, mode=0o700, exist_ok=True)
        fsutil.atomic_write(_pid_path(), "%d\n" % pid, mode=0o600)
    except OSError as e:
        print(f"server_mac: PID-Datei nicht geschrieben: {e}",
              file=sys.stderr, flush=True)


def _forget_pid(pid):
    """PID-Datei entfernen, aber nur, solange sie noch auf dieses Kind zeigt."""
    try:
        with open(_pid_path(), encoding="utf-8") as f:
            if f.read().strip() == str(pid):
                os.remove(_pid_path())
    except (OSError, ValueError):
        pass


def _remove_pid_file():
    try:
        os.remove(_pid_path())
    except OSError:
        pass


def _read_pid():
    """PID aus der PID-Datei oder None (fehlt, unlesbar, keine echte PID).
    PID 0 und 1 scheiden aus: os.kill(0) träfe die eigene Prozessgruppe."""
    try:
        with open(_pid_path(), encoding="utf-8") as f:
            value = f.read().strip()
    except (OSError, ValueError):
        return None
    if not (value.isascii() and value.isdigit()) or int(value) <= 1:
        return None
    return int(value)


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundled_server_bin():
    """whisper-server im .app-Bundle (Contents/Resources/whisper/), wenn wir
    als PyInstaller-App laufen — sonst None. sys.executable ist dann
    .../Quassel.app/Contents/MacOS/Quassel."""
    if not getattr(sys, "frozen", False):
        return None
    contents = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    cand = os.path.join(contents, "Resources", "whisper", "whisper-server")
    return cand if os.access(cand, os.X_OK) else None


def server_bin():
    """Pfad zum whisper-server-Binary: server.env, sonst das Bundle (gefroren),
    sonst der vendor-Build (Entwicklung aus dem Repo)."""
    env = config.read_serverenv()
    cand = env.get("SERVER_BIN", "")
    if cand and os.access(cand, os.X_OK):
        return cand
    bundled = bundled_server_bin()
    if bundled:
        return bundled
    vendor = os.path.join(_repo_root(), "vendor", "whisper.cpp",
                          "build", "bin", "whisper-server")
    return vendor if os.access(vendor, os.X_OK) else None


def current_model():
    """MODEL_PATH aus server.env, falls die Datei existiert, sonst None."""
    env = config.read_serverenv()
    path = env.get("MODEL_PATH", "")
    return path if path and os.path.exists(path) else None


def _find_model():
    """Passendstes vorhandenes Modell im models-Ordner (hwdetect-Default,
    sonst das erste vorhandene aus config.MODELS)."""
    preferred = hwdetect.default_model_for_hardware()
    for name in [preferred] + config.MODELS:
        p = os.path.join(MODEL_DIR, f"ggml-{name}.bin")
        if os.path.exists(p) and os.path.getsize(p) > 1024:
            return p
    return None


def vad_model_path():
    p = os.path.join(MODEL_DIR, config.VAD_MODEL_FILE)
    return p if os.path.exists(p) and os.path.getsize(p) > 1024 else None


def ensure_env():
    """server.env vervollständigen (nur fehlende Schlüssel, mit einer einzigen
    Ausnahme: der überholte WHISPER_DECODE-Vorgabewert "-bs 5" wird gehoben,
    siehe Kommentar dort). True, wenn danach Binary + Modell vorhanden sind."""
    env = config.read_serverenv()
    changed = False
    if not env.get("SERVER_BIN") or not os.access(env.get("SERVER_BIN", ""), os.X_OK):
        binpath = server_bin()
        if binpath:
            env["SERVER_BIN"] = binpath
            changed = True
    if current_model() is None:
        model = _find_model()
        if model:
            env["MODEL_PATH"] = model
            changed = True
    if not env.get("WHISPER_THREADS"):
        env["WHISPER_THREADS"] = str(min(8, os.cpu_count() or 4))
        changed = True
    if not env.get("REQUEST_PATH") and _supports_request_path(env.get("SERVER_BIN", "")):
        env["REQUEST_PATH"] = "/q/" + secrets.token_urlsafe(24)
        changed = True
    if not env.get("WHISPER_DECODE"):
        # Gierige Suche: eigene Messung über 36 Dateien/938 Referenzwörter
        # zeigte beam_size=5 in keiner Datei besser, in einer verrauschten
        # sogar schlechter, bei ~6% mehr Zeit (Median 0,595s vs. 0,557s).
        env["WHISPER_DECODE"] = "-bs 1"
        changed = True
    elif " ".join(env["WHISPER_DECODE"].split()) == "-bs 5":
        # Einmalige Migration, NICHT die allgemeine "füllt nur Fehlendes"-Regel:
        # "-bs 5" war der frühere Vorgabewert dieses Programms selbst (kein
        # Nutzerwille), durch obige Messung abgelöst -- Bestandsnutzer sollen
        # die schnellere Suche auch ohne Neuinstallation bekommen. Jeder ANDERE
        # Wert (eigene Wahl, "-bs 8", "-nf", zusätzliche Flags) bleibt
        # unangetastet: das erkennt man nicht als "alter Default" wieder.
        env["WHISPER_DECODE"] = "-bs 1"
        changed = True
    if not env.get("VAD_MODEL"):
        vad = vad_model_path()
        if vad:
            env["VAD_MODEL"] = vad
            changed = True
    if changed:
        config.write_serverenv(env)
    return bool(env.get("SERVER_BIN")) and current_model() is not None


def build_args(env):
    """Serverargumente aus server.env (Spiegel der systemd-ExecStart-Zeile)."""
    args = [env["SERVER_BIN"], "-m", env["MODEL_PATH"],
            "-t", env.get("WHISPER_THREADS", "4")]
    # Even an older/custom server.env must not enable transcript printing in
    # the diagnostic log. whisper.cpp defaults both print switches to false.
    print_switches = {"-pr", "--print-realtime", "-pp", "--print-progress"}
    args += [arg for arg in env.get("WHISPER_DECODE", "-nf").split()
             if arg not in print_switches]
    vad = env.get("VAD_MODEL", "")
    if vad and os.path.exists(vad):
        args += ["--vad", "--vad-model", vad]
    args += ["--host", HOST, "--port", PORT, "-l", "auto", "-nt"]
    request_path = env.get("REQUEST_PATH", "")
    if request_path and _supports_request_path(env.get("SERVER_BIN", "")):
        args += ["--request-path", request_path]
    return args


def _supports_request_path(binary):
    """Bounded, cached --help capability probe; it does not start a server."""
    if not binary:
        return False
    if binary not in _request_path_support:
        try:
            r = subprocess.run([binary, "--help"], capture_output=True, text=True,
                               timeout=2, check=False)
            _request_path_support[binary] = "--request-path" in (r.stdout + r.stderr)
        except (OSError, subprocess.SubprocessError):
            _request_path_support[binary] = False
    return _request_path_support[binary]


def port_in_use(timeout=0.5):
    """True, wenn auf 127.0.0.1:8765 schon etwas antwortet (Connect-Test)."""
    try:
        with socket.create_connection((HOST, int(PORT)), timeout=timeout):
            return True
    except OSError:
        return False


def start():
    """whisper-server starten (idempotent). Wird auch von
    whisperclient.STARTER gerufen, wenn der Server nicht erreichbar ist.
    Läuft schon ein Server auf dem Port (z.B. vom mac_app-Prozess gestartet,
    während wir im Daemon-Prozess sind), wird KEIN zweiter gespawnt."""
    global _proc
    if _proc is not None:
        if _proc.poll() is None:
            return True
        _proc.wait()          # von selbst gestorbenes Kind ernten (kein Zombie)
        _proc = None
    if port_in_use():
        return True
    if not ensure_env():
        print("server_mac: kein Server-Binary oder Modell gefunden",
              file=sys.stderr, flush=True)
        return False
    env = config.read_serverenv()
    # Upstream examples/server/server.cpp defaults print_realtime and
    # print_progress to false. build_args removes -pr/-pp from config too.
    _proc = subprocess.Popen(
        build_args(env), cwd=os.path.dirname(env["SERVER_BIN"]) or None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True)
    _write_pid(_proc.pid)
    if isinstance(_proc.stdout, io.IOBase):
        threading.Thread(target=_drain_log, args=(_proc.stdout,), daemon=True).start()
    return True


def terminate_group(proc, timeout=5):
    """Prozessgruppe eines mit start_new_session gestarteten Kinds beenden
    und das Kind ernten (TERM, nach timeout KILL)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, TypeError):
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, TypeError):
            proc.kill()
        proc.wait()


def stop():
    """Eigenen Server beenden. Beendet NUR das selbst gestartete Kind —
    Waisen aus abgestürzten Läufen räumt kill_orphans() beim Start weg."""
    global _proc
    if _proc is not None:
        pid = _proc.pid
        if _proc.poll() is None:
            terminate_group(_proc)
        else:
            _proc.wait()
        _forget_pid(pid)
    _proc = None


def _is_own_server(pid, binpath):
    """Läuft unter dieser PID noch unser Server? argv muss mit dem Binärpfad
    beginnen und --port PORT als eigenes Argumentpaar enthalten."""
    ps = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                        capture_output=True, text=True, check=False)
    argv = ps.stdout.strip()
    rest = argv[len(binpath):]
    if not argv.startswith(binpath) or (rest and not rest[0].isspace()):
        return False
    words = rest.split()
    # Port als eigenes Argument, nicht als Teilstring: ein Profil auf
    # einem anderen Port darf nie den Server der installierten App treffen.
    return any(a == "--port" and b == str(PORT) for a, b in zip(words, words[1:]))


def kill_orphans():
    """Server-Waisen aus abgestürzten früheren Läufen beenden. Liest nur die
    PID-Datei des Profils; vor dem SIGTERM prüft _is_own_server() per ps, dass
    diese PID noch unser Server ist (PIDs werden wiederverwendet). Ohne Datei
    passiert nichts; eine veraltete Datei wird entfernt."""
    binpath = server_bin()
    if not binpath:
        return
    pid = _read_pid()
    try:
        if pid is not None and _is_own_server(pid, binpath):
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    finally:
        _remove_pid_file()
