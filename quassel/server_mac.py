"""whisper-server-Verwaltung für macOS (Gegenstück zu quassel/win/server.py).

Kein systemd auf dem Mac — der Server läuft als Kindprozess der App.
Konfiguration über dieselbe server.env wie auf Linux/Windows:
  SERVER_BIN       Pfad zum whisper-server-Binary (Metal-Build)
  MODEL_PATH       ggml-Modell unter ~/Library/Application Support/Quassel/models
  WHISPER_THREADS  Threads (bis 8)
  WHISPER_DECODE   Decode-Flags (Metal-GPU -> Beam-Search "-bs 5")
  VAD_MODEL        Silero-VAD-Modell (optional)

ensure_env() füllt fehlende Einträge mit Hardware-Defaults, überschreibt aber
nie eine schon getroffene Wahl (gleiches Prinzip wie install.sh/win-provision).
"""
import os
import signal
import socket
import subprocess
import sys

from . import config, hwdetect

MODEL_DIR = os.path.join(config.DATADIR, "models")
HOST, PORT = "127.0.0.1", "8765"

_proc = None


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
    """server.env vervollständigen (nur fehlende Schlüssel). True, wenn danach
    Binary + Modell vorhanden sind."""
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
    if not env.get("WHISPER_DECODE"):
        env["WHISPER_DECODE"] = "-bs 5"    # Metal = GPU -> Beam-Search
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
    args += env.get("WHISPER_DECODE", "-nf").split()
    vad = env.get("VAD_MODEL", "")
    if vad and os.path.exists(vad):
        args += ["--vad", "--vad-model", vad]
    args += ["--host", HOST, "--port", PORT, "-l", "auto", "-nt"]
    return args


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
    _proc = subprocess.Popen(
        build_args(env), cwd=os.path.dirname(env["SERVER_BIN"]) or None,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
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
        if _proc.poll() is None:
            terminate_group(_proc)
        else:
            _proc.wait()
    _proc = None


def kill_orphans():
    """Server-Waisen aus abgestürzten früheren Läufen beenden. Trifft nur
    Prozesse, deren argv exakt mit unserem SERVER_BIN-Pfad beginnt UND
    unseren Port enthält (pgrep-Kandidaten, dann per ps verifiziert) —
    nie fremde Prozesse, die den Namen nur irgendwo in der Kommandozeile
    tragen."""
    binpath = server_bin()
    if not binpath:
        return
    try:
        r = subprocess.run(["pgrep", "-f", binpath],
                           capture_output=True, text=True, check=False)
        for pid in r.stdout.split():
            ps = subprocess.run(["ps", "-o", "command=", "-p", pid],
                                capture_output=True, text=True, check=False)
            argv = ps.stdout.strip()
            if argv.startswith(binpath) and ("--port " + PORT) in argv:
                os.kill(int(pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass
