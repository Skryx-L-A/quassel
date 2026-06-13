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

from .. import config, hwdetect
from ..net import download
from ..whisperclient import server_up

BIN_DIR = os.path.join(config.DATADIR, "whisper-bin")
MODEL_DIR = os.path.join(config.DATADIR, "models")
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
        os.replace(tmp, target)
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


def provision(progress=lambda frac, what: None):
    """Erstausstattung: alle Modelle + alle Engines bereitstellen, passende
    Engine aktiv schalten, Standardmodell per Hardware wählen.

    Nutzt ein Offline-Bundle, falls vorhanden, sonst Download. Fuellt nur
    Fehlendes auf und ueberschreibt NICHTS, was schon da ist: eine bereits
    vorhandene (evtl. neuere) Engine bleibt aktiv, ein bereits gewaehltes
    Modell bleibt gewaehlt. Idempotent, auch bei einer erneuten Installation."""
    bundle = bundle_dir()
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(ENGINES_DIR, exist_ok=True)

    for model in config.MODELS:
        _provide_model(model, bundle, progress)      # vorhandene werden uebersprungen
    for kind in ENGINE_ZIPS:
        _provide_engine_zip(kind, bundle, progress)  # vorhandene werden uebersprungen

    # Engine NUR aktivieren, wenn noch keine lauffaehige vorhanden ist — so
    # bleibt z.B. ein schon installierter neuerer CUDA-Build unangetastet
    # (_activate_engine wuerde whisper-bin sonst leeren und neu entpacken).
    if server_exe() is None:
        kind = preferred_engine_kind()
        if not _activate_engine(kind, progress) and kind != "cpu":
            _activate_engine("cpu", progress)

    env = config.read_serverenv()
    # Standardmodell nur waehlen, wenn noch keins (gueltiges) eingetragen ist —
    # eine bereits getroffene Modellwahl wird nicht ueberschrieben.
    if current_model() is None:
        default = hwdetect.default_model_for_hardware()
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
    GPU-Build (z.B. fehlende Treiber/DLLs), wird auf den CPU-Build
    zurückgefallen (erst lokaler Vorrat, dann Download) und erneut probiert."""
    start()
    for _ in range(60):
        if server_up():
            return True
        if _proc is not None and _proc.poll() is not None:
            break               # Server-Prozess ist gestorben
        time.sleep(1)
    if server_up():
        return True
    stop()
    if not _provide_engine_zip("cpu", bundle_dir(), progress):
        return False
    if not _activate_engine("cpu", progress):
        return False
    env = config.read_serverenv()
    env["SERVER_BIN"] = server_exe() or ""
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
    _proc = subprocess.Popen(
        [exe, "-m", model, "--host", "127.0.0.1", "--port", "8765",
         "-l", "auto", "-nt"],
        cwd=os.path.dirname(exe), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creationflags)


def stop():
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    else:
        # Verwaiste Server aus früheren App-Läufen beenden — sonst hält
        # einer den Port 8765 und ein Modellwechsel greift nie.
        subprocess.run(
            ["taskkill", "/F", "/IM", "whisper-server.exe"], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _proc = None
