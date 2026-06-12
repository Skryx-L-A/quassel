"""Robuste Downloads für Quassel.

Nutzt curl (auf Windows 10+ und allen Linux-Zielen vorhanden) statt
urllib — Pythons urllib kann auf Windows still hängen (Proxy/TLS).
Fortschritt wird über die wachsende Zieldatei gemeldet.
"""
import os
import shutil
import subprocess
import threading
import time
import urllib.request

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


def download(url, target, progress=None):
    """Lädt url nach target. progress(frac 0..1) optional. True bei Erfolg."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".part"
    if shutil.which("curl"):
        total = _content_length(url) if progress else 0
        proc = subprocess.Popen(
            ["curl", "-L", "-sS", "--fail", "-o", tmp, url],
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
        proc.wait()
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
