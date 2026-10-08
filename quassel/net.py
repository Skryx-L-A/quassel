"""Robuste Downloads für Quassel.

Nutzt curl (auf Windows 10+ und allen Linux-Zielen vorhanden) statt
urllib — Pythons urllib kann auf Windows still hängen (Proxy/TLS).
Fortschritt wird über die wachsende Zieldatei gemeldet.
"""
import os
import hashlib
import shutil
import subprocess
import threading
import time
import urllib.request

# Hugging Face LFS object IDs, retrieved 2026-10-09 from the model-tree APIs:
# ggerganov/whisper.cpp and ggml-org/whisper-vad.  They are SHA-256 hashes.
SHA256 = {
    "ggml-tiny.bin": "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
    "ggml-base.bin": "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
    "ggml-base-q5_1.bin": "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
    "ggml-small.bin": "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
    "ggml-small-q5_1.bin": "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
    "ggml-medium.bin": "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208",
    "ggml-medium-q5_0.bin": "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f",
    "ggml-large-v3-turbo.bin": "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
    "ggml-large-v3-turbo-q5_0.bin": "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    "ggml-large-v3.bin": "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2",
    "ggml-silero-v5.1.2.bin": "29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf",
}


def _matches_hash(path, expected):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected

NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _content_length(url):
    try:
        r = subprocess.run(["curl", "-sIL", "-m", "20", url],
                           capture_output=True, text=True, check=False, **NOWIN)
        for line in r.stdout.lower().splitlines():
            if line.startswith("content-length:"):
                return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return 0


def download(url, target, progress=None, cancel=None):
    """Lädt url nach target. progress(frac 0..1) optional. True bei Erfolg."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".part"
    if shutil.which("curl"):
        total = _content_length(url) if progress else 0
        proc = subprocess.Popen(
            ["curl", "-L", "-sS", "--fail", "-C", "-", "--retry", "3",
             "--speed-time", "30", "--speed-limit", "1024", "-o", tmp, url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NOWIN)
        if progress and total:
            def poll():
                while proc.poll() is None:
                    try:
                        progress(min(os.path.getsize(tmp) / total, 1.0))
                    except OSError:
                        pass
                    time.sleep(0.4)
            threading.Thread(target=poll, daemon=True).start()
        while proc.poll() is None:
            if cancel and cancel.is_set():
                proc.terminate()
                break
            time.sleep(0.1)
        ok = proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0
    else:
        try:
            def hook(blocks, bs, total):
                if progress and total > 0:
                    progress(min(blocks * bs / total, 1.0))
            urllib.request.urlretrieve(url, tmp, hook if progress else None)
            ok = os.path.exists(tmp) and os.path.getsize(tmp) > 0
        except OSError:
            ok = False
    expected = SHA256.get(os.path.basename(target))
    if ok and expected:
        ok = _matches_hash(tmp, expected)
    if ok:
        os.replace(tmp, target)
        if progress:
            progress(1.0)
    else:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return ok
