"""whisper-server.exe-Verwaltung für Windows.

Erstausstattung (Binaries + Modell) lädt die App beim ersten Start selbst
herunter — der Installer bleibt dadurch winzig. NVIDIA wird über
nvidia-smi erkannt (cuBLAS-Build), sonst CPU-Build.
"""
import os
import shutil
import subprocess
import urllib.request
import zipfile

from .. import config

BIN_DIR = os.path.join(config.DATADIR, "whisper-bin")
MODEL_DIR = os.path.join(config.DATADIR, "models")
WHISPER_RELEASE = "https://github.com/ggml-org/whisper.cpp/releases/latest/download/{}"
ZIP_CPU = "whisper-bin-x64.zip"
ZIP_CUDA = "whisper-cublas-12.4.0-bin-x64.zip"

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


def installed():
    return server_exe() is not None and current_model() is not None


def current_model():
    env = config.read_serverenv()
    path = env.get("MODEL_PATH", "")
    return path if path and os.path.exists(path) else None


def download_binaries(progress=lambda frac, what: None):
    """Lädt whisper.cpp-Binaries (einmalig). progress(0..1, beschreibung)."""
    os.makedirs(BIN_DIR, exist_ok=True)
    zip_name = ZIP_CUDA if has_nvidia() else ZIP_CPU
    target = os.path.join(BIN_DIR, zip_name)

    def hook(blocks, bs, total):
        if total > 0:
            progress(min(blocks * bs / total, 1.0), zip_name)
    urllib.request.urlretrieve(WHISPER_RELEASE.format(zip_name), target, hook)
    with zipfile.ZipFile(target) as z:
        z.extractall(BIN_DIR)
    os.remove(target)
    return server_exe() is not None


def download_model(model, progress=lambda frac, what: None):
    os.makedirs(MODEL_DIR, exist_ok=True)
    modelfile = f"ggml-{model}.bin"
    target = os.path.join(MODEL_DIR, modelfile)
    if not (os.path.exists(target) and os.path.getsize(target) > 1024):
        def hook(blocks, bs, total):
            if total > 0:
                progress(min(blocks * bs / total, 1.0), modelfile)
        urllib.request.urlretrieve(config.MODEL_URL.format(model), target, hook)
    env = config.read_serverenv()
    env["MODEL_PATH"] = target
    env["SERVER_BIN"] = server_exe() or ""
    config.write_serverenv(env)
    return target


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
    _proc = subprocess.Popen(
        [exe, "-m", model, "--host", "127.0.0.1", "--port", "8765",
         "-l", "auto", "-nt"],
        cwd=os.path.dirname(exe),
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
    _proc = None
