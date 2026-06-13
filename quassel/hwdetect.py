"""Hardware-Erkennung -> sinnvolles Standard-Whisper-Modell.

Plattformsicher (nvidia-smi / wmic / sysconf gekapselt, alles in try/except).
Wird beim ersten Start bzw. beim Offline-Provisioning genutzt, um je nach
Hardware ein passendes Default-Modell zu wählen. Der Nutzer kann es danach
jederzeit in den Einstellungen umstellen — alle Modelle sind im Offline-Paket
ohnehin vorhanden.

Mapping:
    NVIDIA-VRAM >= 6144 MB           -> large-v3-turbo
    NVIDIA-VRAM  < 6144 MB (>0)      -> medium
    keine NVIDIA, >=8 Kerne & >=16GB -> medium
    >=4 Kerne                        -> small
    sonst                            -> base
"""
import os
import subprocess

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _run(cmd):
    kw = {"capture_output": True, "text": True, "timeout": 4}
    if os.name == "nt":
        kw["creationflags"] = _NO_WINDOW
    try:
        r = subprocess.run(cmd, **kw)
        if r.returncode == 0:
            return r.stdout
    except Exception:   # noqa: BLE001 — Hilfsprozess fehlt/hängt -> 0/Default
        pass
    return ""


def nvidia_vram_mb():
    """Größtes erkanntes NVIDIA-GPU-VRAM in MB, sonst 0."""
    best = 0
    for line in _run(["nvidia-smi", "--query-gpu=memory.total",
                      "--format=csv,noheader,nounits"]).splitlines():
        line = line.strip()
        if line.isdigit():
            best = max(best, int(line))
    return best


def total_ram_gb():
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30
    except (ValueError, OSError):
        pass
    if os.name == "nt":
        for tok in _run(["wmic", "ComputerSystem", "get",
                         "TotalPhysicalMemory"]).split():
            if tok.isdigit():
                return int(tok) / 2**30
    return 0.0


def default_model_for_hardware():
    """Eines der Modelle aus config.MODELS, passend zur erkannten Hardware."""
    vram = nvidia_vram_mb()
    if vram >= 6144:
        return "large-v3-turbo"
    if vram > 0:
        return "medium"
    cores = os.cpu_count() or 1
    if cores >= 8 and total_ram_gb() >= 16:
        return "medium"
    if cores >= 4:
        return "small"
    return "base"
