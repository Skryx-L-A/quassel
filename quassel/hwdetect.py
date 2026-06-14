"""Hardware-Erkennung für die Wahl eines passenden Standard-Whisper-Modells.

Reine Standardbibliothek und plattformsicher: jede Sonde (nvidia-smi für die
GPU, ctypes/sysconf für den RAM) ist gekapselt und liefert bei jedem Fehler
einen neutralen Wert, sodass die Modellwahl nie eine Exception wirft. Wird
sowohl unter Windows als auch unter Linux beim Erstausstatten benutzt.
"""
import os
import subprocess

# Kein Konsolenblitz unter Windows, wenn nvidia-smi aus der Fenster-exe läuft.
_NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def nvidia_vram_mb():
    """Größter VRAM (in MB) einer NVIDIA-GPU, sonst None (keine/kein Treiber)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6, check=False, **_NOWIN)
    except (OSError, subprocess.SubprocessError):
        return None
    vals = []
    for line in out.stdout.splitlines():
        line = line.strip()
        try:
            vals.append(int(line))
        except ValueError:
            continue
    return max(vals) if vals else None


def cpu_core_count():
    """Logische CPU-Kerne (mindestens 1)."""
    return os.cpu_count() or 1


def total_ram_gb():
    """Physischer RAM in GiB, oder None wenn nicht ermittelbar."""
    # Linux/Unix: POSIX-sysconf
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names \
                and "SC_PAGE_SIZE" in os.sysconf_names:
            pages = os.sysconf("SC_PHYS_PAGES")
            pagesize = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and pagesize > 0:
                return pages * pagesize / (1024 ** 3)
    except (ValueError, OSError):
        pass
    # Windows: GlobalMemoryStatusEx über ctypes (stdlib, kein wmic nötig)
    if os.name == "nt":
        try:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = _MemStatus()
            stat.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullTotalPhys / (1024 ** 3)
        except (OSError, AttributeError, ValueError):
            pass
    return None


def default_model_for_hardware():
    """Wählt ein Standard-Whisper-Modell passend zur Hardware.

    Mit NVIDIA-GPU laufen die vollen Modelle flott:
      VRAM >= 6144 MB -> large-v3-turbo
      VRAM <  6144 MB -> medium
    Ohne GPU (reine CPU) werden QUANTISIERTE (q5) Modelle gewählt — auf CPU
    deutlich schneller bei nahezu gleicher Genauigkeit ("balanced"):
      Kerne >= 8 und RAM >= 16 GB -> medium-q5_0
      Kerne >= 4                  -> small-q5_1
      sonst                       -> base-q5_1
    """
    vram = nvidia_vram_mb()
    if vram is not None:
        return "large-v3-turbo" if vram >= 6144 else "medium"
    cores = cpu_core_count()
    ram = total_ram_gb() or 0.0
    if cores >= 8 and ram >= 16:
        return "medium-q5_0"
    if cores >= 4:
        return "small-q5_1"
    return "base-q5_1"
