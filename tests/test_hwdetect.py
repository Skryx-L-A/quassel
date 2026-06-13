"""Tests der Hardware-basierten Standard-Modellwahl (alle Zweige gemockt).

Die drei Sonden (nvidia_vram_mb, cpu_core_count, total_ram_gb) werden direkt
am Modul ersetzt, sodass kein echtes nvidia-smi/ctypes nötig ist und der Test
auf jeder CI-Maschine identisch läuft."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import hwdetect


def _mock(vram, cores, ram):
    hwdetect.nvidia_vram_mb = lambda: vram
    hwdetect.cpu_core_count = lambda: cores
    hwdetect.total_ram_gb = lambda: ram


def test_nvidia_high_vram_turbo():
    _mock(6144, 4, 8)            # genau an der Schwelle
    assert hwdetect.default_model_for_hardware() == "large-v3-turbo"
    _mock(24576, 32, 64)
    assert hwdetect.default_model_for_hardware() == "large-v3-turbo"


def test_nvidia_low_vram_medium():
    _mock(4096, 16, 32)          # NVIDIA, aber < 6144 MB -> medium trotz starker CPU
    assert hwdetect.default_model_for_hardware() == "medium"
    _mock(6143, 4, 8)            # eins unter der Schwelle
    assert hwdetect.default_model_for_hardware() == "medium"


def test_no_nvidia_strong_cpu_medium():
    _mock(None, 8, 16)           # genau an den Schwellen
    assert hwdetect.default_model_for_hardware() == "medium"
    _mock(None, 12, 32)
    assert hwdetect.default_model_for_hardware() == "medium"


def test_no_nvidia_enough_cores_small():
    _mock(None, 4, 8)            # >= 4 Kerne, aber RAM/Kerne zu wenig für medium
    assert hwdetect.default_model_for_hardware() == "small"
    _mock(None, 8, 8)            # 8 Kerne aber nur 8 GB RAM -> medium-Zweig fällt
    assert hwdetect.default_model_for_hardware() == "small"
    _mock(None, 16, 12)          # viele Kerne, RAM < 16 -> small
    assert hwdetect.default_model_for_hardware() == "small"


def test_weak_machine_base():
    _mock(None, 2, 4)
    assert hwdetect.default_model_for_hardware() == "base"
    _mock(None, 1, 2)
    assert hwdetect.default_model_for_hardware() == "base"


def test_ram_none_is_safe():
    _mock(None, 8, None)         # RAM nicht ermittelbar -> wie 0, medium-Zweig fällt
    assert hwdetect.default_model_for_hardware() == "small"


if __name__ == "__main__":
    for fn in [test_nvidia_high_vram_turbo, test_nvidia_low_vram_medium,
               test_no_nvidia_strong_cpu_medium, test_no_nvidia_enough_cores_small,
               test_weak_machine_base, test_ram_none_is_safe]:
        fn(); print("ok:", fn.__name__)
    print("ALL HWDETECT TESTS PASSED")
