"""Tests der Hardware-basierten Standard-Modellwahl (alle Zweige gemockt).

Die drei Sonden (nvidia_vram_mb, cpu_core_count, total_ram_gb) werden direkt
am Modul ersetzt, sodass kein echtes nvidia-smi/ctypes nötig ist und der Test
auf jeder CI-Maschine identisch läuft."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from quassel import hwdetect

# Original-Sonden sichern; nach jedem Test zurücksetzen (kein Modul-Zustand
# leakt in andere Testdateien)
_ORIG_PROBES = (hwdetect.is_apple_silicon, hwdetect.nvidia_vram_mb,
                hwdetect.cpu_core_count, hwdetect.total_ram_gb)


@pytest.fixture(autouse=True)
def _restore_probes():
    yield
    (hwdetect.is_apple_silicon, hwdetect.nvidia_vram_mb,
     hwdetect.cpu_core_count, hwdetect.total_ram_gb) = _ORIG_PROBES


def _mock(vram, cores, ram, apple=False):
    hwdetect.is_apple_silicon = lambda: apple
    hwdetect.nvidia_vram_mb = lambda: vram
    hwdetect.cpu_core_count = lambda: cores
    hwdetect.total_ram_gb = lambda: ram


def test_apple_silicon_turbo_q5():
    # Apple Silicon (Metal-GPU): großes Turbo-Modell quantisiert, egal wie
    # die (dort irrelevanten) NVIDIA-/Kern-Sonden aussehen.
    _mock(None, 12, 48, apple=True)
    assert hwdetect.default_model_for_hardware() == "large-v3-turbo-q5_0"
    _mock(8192, 4, 16, apple=True)   # hypothetisches nvidia-smi darf nicht gewinnen
    assert hwdetect.default_model_for_hardware() == "large-v3-turbo-q5_0"


def test_is_apple_silicon_probe(monkeypatch):
    import platform as _pl
    probe = _ORIG_PROBES[0]      # echte Sonde, unabhängig von _mock-Stubs
    monkeypatch.setattr(hwdetect.sys, "platform", "darwin")
    monkeypatch.setattr(_pl, "machine", lambda: "arm64")
    assert probe() is True
    monkeypatch.setattr(hwdetect.sys, "platform", "linux")
    assert probe() is False


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
    # Ohne GPU: höchstens small-q5_1 (medium/large auf CPU zu langsam fürs Live-Diktat)
    _mock(None, 8, 16)
    assert hwdetect.default_model_for_hardware() == "small-q5_1"
    _mock(None, 12, 32)
    assert hwdetect.default_model_for_hardware() == "small-q5_1"


def test_no_nvidia_enough_cores_small():
    _mock(None, 4, 8)            # >= 4 Kerne, aber RAM/Kerne zu wenig für medium
    assert hwdetect.default_model_for_hardware() == "small-q5_1"
    _mock(None, 8, 8)            # 8 Kerne aber nur 8 GB RAM -> medium-Zweig fällt
    assert hwdetect.default_model_for_hardware() == "small-q5_1"
    _mock(None, 16, 12)          # viele Kerne, RAM < 16 -> small-q5_1
    assert hwdetect.default_model_for_hardware() == "small-q5_1"


def test_weak_machine_base():
    _mock(None, 2, 4)
    assert hwdetect.default_model_for_hardware() == "base-q5_1"
    _mock(None, 1, 2)
    assert hwdetect.default_model_for_hardware() == "base-q5_1"


def test_ram_none_is_safe():
    _mock(None, 8, None)         # RAM nicht ermittelbar -> wie 0, medium-Zweig fällt
    assert hwdetect.default_model_for_hardware() == "small-q5_1"


if __name__ == "__main__":
    for fn in [test_nvidia_high_vram_turbo, test_nvidia_low_vram_medium,
               test_no_nvidia_strong_cpu_medium, test_no_nvidia_enough_cores_small,
               test_weak_machine_base, test_ram_none_is_safe]:
        fn(); print("ok:", fn.__name__)
    print("ALL HWDETECT TESTS PASSED")
