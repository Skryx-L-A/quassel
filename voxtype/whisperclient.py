"""Client für den lokalen whisper.cpp-Server (voxtype-server.service)."""
import subprocess
import time

from . import config

SERVER = "http://127.0.0.1:8765"
SERVICE = "voxtype-server.service"


def _default_starter():
    subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)


# Plattform-Hook: Windows ersetzt das durch den eigenen Server-Prozessstart
STARTER = _default_starter


def server_up(timeout=2):
    return subprocess.run(
        ["curl", "-fsS", "-m", str(timeout), "-o", "/dev/null", SERVER + "/"],
        check=False).returncode == 0


def ensure_server():
    if server_up():
        return True
    STARTER()
    for _ in range(240):
        if server_up():
            return True
        time.sleep(0.5)
    return False


def transcribe(wavpath, cfg, timeout=120):
    """Transkribiert eine WAV-Datei; None bei Fehler."""
    args = ["curl", "-fsS", "-m", str(timeout), SERVER + "/inference",
            "-F", f"file=@{wavpath}",
            "-F", "response_format=text", "-F", "temperature=0.0"]
    if cfg.language != "auto":
        args += ["-F", f"language={cfg.language}"]
    words = config.dictionary_words()
    if words:
        args += ["-F", "prompt=" + ", ".join(words[:80])]
    r = subprocess.run(args, capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else None
