"""Review 2026-10-09, Befunde 8 und 9: die Wake-Folgeaufnahme zeigt den Pegel
ihrer eigenen Rohdatei und stört kein parallel gestartetes Tastatur-Diktat."""
import os
import struct
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

from quassel import audio, dictation, wakeword  # noqa: E402
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


def test_keyboard_dictation_during_followup_keeps_its_pill_pump_and_tone(monkeypatch, tmp_path):
    """Befund 9: Hotkey-Diktat startet, während die Folgeaufnahme läuft."""
    levels = []
    keyboard_pump = SimpleNamespace(stopped=False)
    keyboard_pump.stop = lambda: setattr(keyboard_pump, "stopped", True)

    def record(core):
        core.rec.active = True                  # Tastatur-Diktat läuft jetzt
        core._level_pump = keyboard_pump
        core.states.append(("recording", ""))
        return b"pcm"
    core, sounds = wake_core(monkeypatch, tmp_path, levels, record)
    core._wake_record_followup()
    assert keyboard_pump.stopped is False
    assert core._level_pump is keyboard_pump
    assert core.states[-1] == ("recording", "")
    assert sounds == ["start"]                  # kein Stopp-Ton mitten ins Diktat


def test_followup_is_skipped_while_a_keyboard_dictation_runs(monkeypatch, tmp_path):
    def record(core):
        raise AssertionError("darf nicht aufnehmen")
    core, sounds = wake_core(monkeypatch, tmp_path, [], record)
    core.rec.active = True
    assert core._wake_record_followup() is None
    assert core.states == [] and sounds == []


def test_listener_checks_busy_before_the_followup():
    busy = []
    calls = []
    cfg = SimpleNamespace(wakeword_phrase="hey quassel", wakeword_enabled=True)
    listener = wakeword.WakeListener(
        cfg, lambda: b"a", lambda pcm: "hey quassel", calls.append,
        is_busy=lambda: bool(busy),
        record_followup=lambda: calls.append("followup") or b"b")

    def transcribe(pcm):
        busy.append(1)                          # Hotkey während der Erkennung
        return "hey quassel"
    listener._transcribe = transcribe
    listener.run_once()
    assert "followup" not in calls
