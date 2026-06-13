"""hwdetect.default_model_for_hardware: alle Hardware-Zweige (Erkennung gemockt)."""
import quassel.hwdetect as hw


def _patch(monkeypatch, vram, cores, ram):
    monkeypatch.setattr(hw, "nvidia_vram_mb", lambda: vram)
    monkeypatch.setattr(hw, "total_ram_gb", lambda: ram)
    monkeypatch.setattr(hw.os, "cpu_count", lambda: cores)


def test_big_gpu_turbo(monkeypatch):
    _patch(monkeypatch, vram=8192, cores=16, ram=32)
    assert hw.default_model_for_hardware() == "large-v3-turbo"


def test_gpu_exactly_6gb_turbo(monkeypatch):
    _patch(monkeypatch, vram=6144, cores=4, ram=8)
    assert hw.default_model_for_hardware() == "large-v3-turbo"


def test_small_gpu_medium(monkeypatch):
    _patch(monkeypatch, vram=4096, cores=4, ram=8)
    assert hw.default_model_for_hardware() == "medium"


def test_no_gpu_strong_cpu_medium(monkeypatch):
    _patch(monkeypatch, vram=0, cores=8, ram=16)
    assert hw.default_model_for_hardware() == "medium"


def test_no_gpu_mid_cpu_small(monkeypatch):
    _patch(monkeypatch, vram=0, cores=4, ram=8)
    assert hw.default_model_for_hardware() == "small"


def test_no_gpu_weak_base(monkeypatch):
    _patch(monkeypatch, vram=0, cores=2, ram=4)
    assert hw.default_model_for_hardware() == "base"


def test_strong_cpu_but_low_ram_falls_to_small(monkeypatch):
    _patch(monkeypatch, vram=0, cores=12, ram=8)   # viele Kerne, aber < 16 GB
    assert hw.default_model_for_hardware() == "small"
