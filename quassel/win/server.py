"""whisper-server.exe-Verwaltung und Erstausstattung für Windows.

Erstausstattung (provision): stellt ALLE Sprachmodelle und ALLE Engines
bereit, schaltet die zur GPU passende Engine aktiv und wählt per Hardware
ein Standardmodell. Quelle ist ein Offline-Bundle (neben der exe oder via
QUASSEL_BUNDLE), falls vorhanden — sonst Download. So muss nach dem
einmaligen Beziehen nie wieder etwas heruntergeladen werden.

Lokale Ablage unter %LOCALAPPDATA%\\Quassel:
  models\\ggml-*.bin        alle Modelle
  engines\\{cpu,blas,cublas}\\*.zip   permanenter Engine-Vorrat
  whisper-bin\\             die aktuell aktive (entpackte) Engine
"""
import os
import shutil
import subprocess
import sys
import time
import zipfile
import secrets
import json
import io
import threading

from .. import config, fsutil, hwdetect, ports
from ..net import download
from ..whisperclient import server_up

BIN_DIR = os.path.join(config.DATADIR, "whisper-bin")
MODEL_DIR = config.MODELDIR
ENGINES_DIR = os.path.join(config.DATADIR, "engines")
WHISPER_RELEASE = "https://github.com/ggml-org/whisper.cpp/releases/latest/download/{}"
# Engine-Art -> Zip-Name. cublas: NVIDIA (CUDA-DLLs im Zip); blas: OpenBLAS
# (schnellster Nicht-NVIDIA-Build, den upstream für Windows anbietet);
# cpu: schlanker Notnagel-Build.
ENGINE_ZIPS = {
    "cpu": "whisper-bin-x64.zip",
    "blas": "whisper-blas-bin-x64.zip",
    "cublas": "whisper-cublas-12.4.0-bin-x64.zip",
}

_proc = None
_request_path_support = {}
PID_FILE = os.path.join(config.DATADIR, "whisper-server.pid.json")
LOG_FILE = os.path.join(config.DATADIR, "whisper-server.log")


def _log_stream(extra=0):
    os.makedirs(config.DATADIR, exist_ok=True)
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) + extra >= 1024 * 1024:
        for n in (2, 1):
            source = LOG_FILE if n == 1 else LOG_FILE + ".1"
            if os.path.exists(source):
                fsutil.replace(source, LOG_FILE + f".{n}")
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


def _write_pid(pid, exe, port):
    os.makedirs(config.DATADIR, exist_ok=True)
    temp = PID_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as out:
        json.dump({"pid": pid, "exe": os.path.normcase(os.path.abspath(exe)),
                   "port": int(port)}, out)
    fsutil.replace(temp, PID_FILE)


def _owned_orphan(record):
    """Require live executable path and listening port before killing a saved PID."""
    pid, exe, port = record["pid"], record["exe"], record["port"]
    if not isinstance(pid, int) or pid <= 0 or not isinstance(port, int):
        return False
    script = ("$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d'; "
              "$c=Get-NetTCPConnection -State Listen -LocalPort %d -ErrorAction SilentlyContinue "
              "| Where-Object OwningProcess -eq %d; "
              "if ($p -and $c) {$p.ExecutablePath}") % (pid, port, pid)
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and os.path.normcase(os.path.abspath(result.stdout.strip())) == exe


def _supports_request_path(binary):
    if not binary:
        return False
    if binary not in _request_path_support:
        try:
            r = subprocess.run([binary, "--help"], capture_output=True, text=True,
                               timeout=2, check=False)
            _request_path_support[binary] = "--request-path" in (r.stdout + r.stderr)
        except (OSError, ValueError, subprocess.SubprocessError):
            _request_path_support[binary] = False
    return _request_path_support[binary]


def server_exe():
    for name in ("whisper-server.exe", "server.exe"):
        p = os.path.join(BIN_DIR, name)
        if os.path.exists(p):
            return p
        for root, _dirs, files in os.walk(BIN_DIR):
            if name in files:
                return os.path.join(root, name)
    return None


def has_nvidia():
    return shutil.which("nvidia-smi") is not None


def preferred_engine_kind():
    """Zur Hardware passende Engine: NVIDIA -> cublas, sonst OpenBLAS."""
    return "cublas" if has_nvidia() else "blas"


def installed():
    return server_exe() is not None and current_model() is not None


def current_model():
    env = config.read_serverenv()
    path = env.get("MODEL_PATH", "")
    return path if path and os.path.exists(path) else None


def bundle_dir():
    """Pfad zum Offline-Payload (models/ + engines/), falls vorhanden.

    Reihenfolge: QUASSEL_BUNDLE, dann ein 'payload'-Ordner neben der exe oder
    eine Ebene darüber (so liegt das Offline-Paket: <root>/Quassel/Quassel.exe
    + <root>/payload). None, wenn kein vollständiges Bundle gefunden wird."""
    cands = []
    env = os.environ.get("QUASSEL_BUNDLE")
    if env:
        cands.append(env)
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    cands += [os.path.join(base, "payload"),
              os.path.join(os.path.dirname(base), "payload")]
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "models")) \
                and os.path.isdir(os.path.join(c, "engines")):
            return os.path.abspath(c)
    return None


# ----------------------------------------------------------------- Bereitstellen
def _have_model(path):
    return os.path.exists(path) and os.path.getsize(path) > 1024


def _copy_file(src, target, progress, label):
    """Große Datei mit Fortschritt kopieren (Bundle -> lokale Ablage)."""
    if not (src and os.path.exists(src)):
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    total = os.path.getsize(src) or 1
    tmp = target + ".part"
    done = 0
    try:
        with open(src, "rb") as r, open(tmp, "wb") as w:
            while True:
                chunk = r.read(4 * 1024 * 1024)
                if not chunk:
                    break
                w.write(chunk)
                done += len(chunk)
                progress(min(done / total, 1.0), label)
        fsutil.replace(tmp, target)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    progress(1.0, label)
    return True


def _provide_model(model, bundle, progress):
    target = os.path.join(MODEL_DIR, f"ggml-{model}.bin")
    if _have_model(target):
        return target
    label = f"ggml-{model}.bin"
    if bundle and _copy_file(os.path.join(bundle, "models", label),
                             target, progress, label):
        return target
    if download(config.MODEL_URL.format(model), target,
                lambda f: progress(f, label)):
        return target
    return None


def vad_model_path():
    """Pfad zum Silero-VAD-Modell, falls vorhanden, sonst None."""
    p = os.path.join(MODEL_DIR, config.VAD_MODEL_FILE)
    return p if os.path.exists(p) and os.path.getsize(p) > 1024 else None


def _provide_vad(bundle, progress):
    """Silero-VAD-Modell (~0.9 MB) bereitstellen — optional, scheitert leise."""
    target = os.path.join(MODEL_DIR, config.VAD_MODEL_FILE)
    if os.path.exists(target) and os.path.getsize(target) > 1024:
        return
    if bundle and _copy_file(os.path.join(bundle, "models", config.VAD_MODEL_FILE),
                             target, progress, config.VAD_MODEL_FILE):
        return
    download(config.VAD_MODEL_URL, target, lambda f: progress(f, config.VAD_MODEL_FILE))


def _provide_engine_zip(kind, bundle, progress):
    zipname = ENGINE_ZIPS[kind]
    dest = os.path.join(ENGINES_DIR, kind, zipname)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    if bundle and _copy_file(os.path.join(bundle, "engines", kind, zipname),
                             dest, progress, zipname):
        return dest
    if download(WHISPER_RELEASE.format(zipname), dest,
                lambda f: progress(f, zipname)):
        return dest
    return None


def _activate_engine(kind, progress):
    """Die gewählte Engine in whisper-bin entpacken (ersetzt eine alte)."""
    zip_path = os.path.join(ENGINES_DIR, kind, ENGINE_ZIPS[kind])
    if not os.path.exists(zip_path):
        return False
    progress(0.0, ENGINE_ZIPS[kind])
    shutil.rmtree(BIN_DIR, ignore_errors=True)
    os.makedirs(BIN_DIR, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(BIN_DIR)
    except (OSError, zipfile.BadZipFile):
        return False
    progress(1.0, ENGINE_ZIPS[kind])
    return server_exe() is not None


def provision(progress=lambda frac, what: None, full=False):
    """Erstausstattung. Standardmaessig SCHLANK: nur die zur Hardware passende
    Engine (NVIDIA -> cuBLAS, sonst OpenBLAS) und EIN Modell
    (hwdetect.default_model_for_hardware()). Mit full=True alle Engines + alle
    5 Modelle (volle Offline-Nutzung).

    Nutzt ein Offline-Bundle, falls vorhanden, sonst Download. Fuellt nur
    Fehlendes auf und ueberschreibt NICHTS, was schon da ist: eine bereits
    vorhandene (evtl. neuere) Engine bleibt aktiv, ein bereits gewaehltes
    Modell bleibt gewaehlt. Idempotent, auch bei einer erneuten Installation."""
    bundle = bundle_dir()
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(ENGINES_DIR, exist_ok=True)

    kind = preferred_engine_kind()
    default = hwdetect.default_model_for_hardware()
    models = config.MODELS if full else [default]
    engines = list(ENGINE_ZIPS) if full else [kind]

    for model in models:
        _provide_model(model, bundle, progress)      # vorhandene werden uebersprungen
    _provide_vad(bundle, progress)                   # Silero-VAD (klein, optional)
    for k in engines:
        _provide_engine_zip(k, bundle, progress)     # vorhandene werden uebersprungen

    # Engine NUR aktivieren, wenn noch keine lauffaehige vorhanden ist — so
    # bleibt z.B. ein schon installierter neuerer CUDA-Build unangetastet
    # (_activate_engine wuerde whisper-bin sonst leeren und neu entpacken).
    if server_exe() is None:
        if not _activate_engine(kind, progress) and kind != "cpu":
            # passende Engine fehlt/scheitert -> CPU-Build nachziehen + aktivieren
            _provide_engine_zip("cpu", bundle, progress)
            _activate_engine("cpu", progress)

    env = config.read_serverenv()
    # Standardmodell nur waehlen, wenn noch keins (gueltiges) eingetragen ist —
    # eine bereits getroffene Modellwahl wird nicht ueberschrieben.
    if current_model() is None:
        model_path = os.path.join(MODEL_DIR, f"ggml-{default}.bin")
        if not _have_model(model_path):           # Notfall: irgendein da liegendes
            for m in config.MODELS:
                p = os.path.join(MODEL_DIR, f"ggml-{m}.bin")
                if _have_model(p):
                    model_path = p
                    break
        env["MODEL_PATH"] = model_path
    env["SERVER_BIN"] = server_exe() or env.get("SERVER_BIN", "")
    config.write_serverenv(env)
    return installed()


def ensure_working(progress=lambda frac, what: None):
    """Startprobe: Server hochfahren und Gesundheit prüfen. Scheitert der
    GPU-Build tatsächlich, hilft der CPU-Build für diesen Start. Ein noch
    ladender Prozess bleibt erhalten und ein CPU-Notlauf wird später erneut
    mit der bevorzugten Engine versucht."""
    env = config.read_serverenv()
    if env.get("ENGINE_RETRY") == "gpu":
        kind = preferred_engine_kind()
        if os.path.exists(os.path.join(ENGINES_DIR, kind, ENGINE_ZIPS[kind])):
            stop()
            if _activate_engine(kind, progress):
                env["SERVER_BIN"] = server_exe() or ""
                env.pop("ENGINE_RETRY", None)
                config.write_serverenv(env)
                progress(0.0, "Retrying GPU engine")
            else:
                _activate_engine("cpu", progress)
                progress(0.0, "GPU retry unavailable; using CPU engine")
    start()
    for _ in range(60):
        if server_up():
            return True
        if _proc is not None and _proc.poll() is not None:
            break               # Server-Prozess ist gestorben
        time.sleep(1)
    if server_up():
        return True
    if _proc is None or _proc.poll() is None:
        progress(0.0, "GPU engine is still loading; retry later")
        return False
    progress(0.0, "GPU engine exited; trying CPU engine")
    stop()
    if not _provide_engine_zip("cpu", bundle_dir(), progress):
        return False
    if not _activate_engine("cpu", progress):
        return False
    env = config.read_serverenv()
    env["SERVER_BIN"] = server_exe() or ""
    if preferred_engine_kind() != "cpu":
        env["ENGINE_RETRY"] = "gpu"
    config.write_serverenv(env)
    start()
    for _ in range(60):
        if server_up():
            return True
        time.sleep(1)
    return False


def start():
    """Startet whisper-server.exe (Idempotent; wird von whisperclient.STARTER
    aufgerufen, wenn der Server nicht erreichbar ist)."""
    global _proc
    if _proc is not None and _proc.poll() is None:
        return
    exe = server_exe()
    model = current_model()
    if not exe or not model:
        return
    # Läuft unser Server schon (OURS) oder ist kein Port frei bzw. QUASSEL_PORT
    # belegt (BUSY, Meldung aus ports.choose()): keinen zweiten starten.
    # Hält ein fremder Dienst den Vorgabeport, weicht choose() aus.
    choice = ports.choose()
    if choice.status != ports.FREE:
        return
    server_env = config.read_serverenv()
    request_path = server_env.get("REQUEST_PATH", "")
    if not request_path and _supports_request_path(exe):
        request_path = "/q/" + secrets.token_urlsafe(24)
        server_env["REQUEST_PATH"] = request_path
        config.write_serverenv(server_env)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # PATH bereinigen: PyInstaller hängt dist\_internal an PATH; der Server
    # würde sonst unsere VC-Runtime-DLLs laden und den dist-Ordner sperren
    # (blockiert Rebuilds, solange der Server läuft).
    env = os.environ.copy()
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if p and not p.startswith(mei))
    # whisper-Threads: bis ~8 (mehr bringt kaum etwas; HT-Kerne wenig).
    # Decode: gierige Suche überall (eigene Messung über 36 Dateien/938
    # Referenzwörter: beam_size=5 in keiner Datei besser, ~6% langsamer) —
    # ohne NVIDIA zusätzlich -nf (kein Temperatur-Fallback, deckelt die
    # Decode-Zeit auf schwacher CPU).
    threads = str(min(8, os.cpu_count() or 4))
    decode = ["-bs", "1"] if has_nvidia() else ["-nf"]
    args = [exe, "-m", model, "-t", threads, *decode,
            "--host", "127.0.0.1", "--port", str(choice.port), "-l", "auto", "-nt"]
    if request_path and _supports_request_path(exe):
        args += ["--request-path", request_path]
    vad = vad_model_path()
    if vad:                                   # Stille überspringen + keine Halluzinationen
        args += ["--vad", "--vad-model", vad]
    # Upstream examples/server/server.cpp defaults print_realtime and
    # print_progress to false; -pr would print speech content in real time.
    # Neither flag is passed here. Source: ggml-org/whisper.cpp server.cpp.
    _proc = subprocess.Popen(
        args, cwd=os.path.dirname(exe), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=creationflags)
    if isinstance(_proc.stdout, io.IOBase):
        threading.Thread(target=_drain_log, args=(_proc.stdout,), daemon=True).start()
    if isinstance(_proc.pid, int):
        _write_pid(_proc.pid, exe, choice.port)


def stop():
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    elif os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, encoding="utf-8") as source:
                record = json.load(source)
            if _owned_orphan(record):
                subprocess.run(["taskkill", "/F", "/PID", str(record["pid"])],
                               check=False, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    _proc = None


def restart_owned():
    """Restart this profile's live server child, never an unrelated listener."""
    owned = _proc is not None and _proc.poll() is None
    if not owned:
        try:
            with open(PID_FILE, encoding="utf-8") as source:
                record = json.load(source)
            owned = (record.get("port") == ports.current()
                     and _owned_orphan(record))
        except (OSError, ValueError, TypeError, AttributeError):
            return False
    if not owned:
        return False
    stop()
    start()
    if _proc is None or _proc.poll() is not None:
        return False
    from .. import selfheal
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if selfheal.functional_probe(timeout=2):
            return True
        time.sleep(1)
    return False
