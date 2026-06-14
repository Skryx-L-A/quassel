"""Client für den lokalen whisper.cpp-Server (quassel-server.service)."""
import os
import subprocess
import time

from . import config

# Windows öffnet sonst für jeden curl-Aufruf kurz ein Konsolenfenster
NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

SERVER = "http://127.0.0.1:8765"
SERVICE = "quassel-server.service"


def _default_starter():
    subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)


# Plattform-Hook: Windows ersetzt das durch den eigenen Server-Prozessstart
STARTER = _default_starter


def server_up(timeout=2):
    return subprocess.run(
        ["curl", "-fsS", "-m", str(timeout), "-o", os.devnull, SERVER + "/"],
        check=False, **NOWIN).returncode == 0


def ensure_server():
    if server_up():
        return True
    STARTER()
    for _ in range(240):
        if server_up():
            return True
        time.sleep(0.5)
    return False


# Zweisprachiger Anstoß für den "mixed"-Modus: primt das Modell darauf,
# englische Begriffe in deutscher Rede englisch zu lassen (Code-Switching).
MIXED_PRIMER = "Das Meeting ist um 3 PM. Let's go - schick mir das Update."


def build_inference_args(wavpath, cfg, words, timeout=120):
    """curl-Argumente für /inference bauen. Sprache:
      auto   -> automatische Erkennung (kein language-Feld)
      mixed  -> automatische Erkennung + zweisprachiger Prompt-Anstoß (#23)
      de/en  -> feste Sprache."""
    args = ["curl", "-fsS", "-m", str(timeout), SERVER + "/inference",
            "-F", f"file=@{wavpath}",
            "-F", "response_format=text", "-F", "temperature=0.0"]
    lang = getattr(cfg, "language", "auto")
    if lang not in ("auto", "mixed"):
        args += ["-F", f"language={lang}"]
    prompt_bits = []
    if lang == "mixed":
        prompt_bits.append(MIXED_PRIMER)
    if words:
        prompt_bits.append(", ".join(words[:80]))
    if prompt_bits:
        args += ["-F", "prompt=" + " ".join(prompt_bits)]
    return args


def transcribe(wavpath, cfg, timeout=120):
    """Transkribiert eine WAV-Datei; None bei Fehler."""
    args = build_inference_args(wavpath, cfg, config.dictionary_words(), timeout)
    r = subprocess.run(args, capture_output=True, text=True, check=False,
                       encoding="utf-8", errors="replace", **NOWIN)
    return r.stdout if r.returncode == 0 else None
