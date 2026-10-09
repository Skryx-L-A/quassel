"""Review 2026-10-09, Befund 8: die Wake-Folgeaufnahme zeigt den Pegel
ihrer eigenen Rohdatei."""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from quassel import audio, dictation  # noqa: E402
from test_dictation_ui import Core  # noqa: E402

LOUD = struct.pack("<h", 6000) * 4000


def wake_core(monkeypatch, tmp_path, levels, record):
    monkeypatch.setattr(dictation, "WAKE_RAW", str(tmp_path / "wake.raw"))
    monkeypatch.setattr(audio, "RAW", str(tmp_path / "rec.raw"))
    monkeypatch.setattr(audio, "RAW_B", str(tmp_path / "rec-b.raw"))
    sounds = []
    monkeypatch.setattr(dictation.beep, "start", lambda: sounds.append("start"))
    monkeypatch.setattr(dictation.beep, "stop", lambda: sounds.append("stop"))
    core = Core(target=levels.append)
    core.cfg.beep = True
    core._wake_record_utterance = lambda spot=True: record(core)
    return core, sounds


def test_followup_level_comes_from_the_wake_recording(monkeypatch, tmp_path):
    """Befund 8: sonst las die Pumpe die alte Diktatdatei -> 0 -> „kein Ton"."""
    levels = []
    stale = tmp_path / "rec.raw"
    stale.write_bytes(LOUD)
    old = time.time() - 60
    os.utime(stale, (old, old))

    def record(core):
        with open(dictation.WAKE_RAW, "wb") as f:
            for _ in range(8):
                f.write(LOUD)
                f.flush()
                time.sleep(0.05)
        return b"pcm"
    core, _ = wake_core(monkeypatch, tmp_path, levels, record)
    assert core._wake_record_followup() == b"pcm"
    assert levels and max(levels) > 0.3


def test_rms_level_reads_a_given_file(tmp_path):
    path = tmp_path / "x.raw"
    path.write_bytes(LOUD)
    assert audio.rms_level(path=str(path)) > 0.3
    assert audio.rms_level(path=str(tmp_path / "missing.raw")) == 0.0

