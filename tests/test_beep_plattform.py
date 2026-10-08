"""Linux Bluetooth start tone with no real sink or audio player."""
import os
from types import SimpleNamespace
import tempfile
import time
import wave

from quassel import beep


def _tone(path):
    with wave.open(str(path), "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(16000)
        sound.writeframes(b"\x01\x02" * 160)


def test_bluetooth_start_tone_has_silence_preroll(tmp_path, monkeypatch):
    tone = tmp_path / "start.wav"
    _tone(tone)
    seen = []
    monkeypatch.setattr(beep, "START_WAV", str(tone))
    monkeypatch.setattr(beep.sys, "platform", "linux")
    monkeypatch.setattr(beep.os, "name", "posix")
    monkeypatch.setattr(beep.shutil, "which", lambda name: name == "pw-play")
    monkeypatch.setattr(beep.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="bluez_output.headset\n"))
    monkeypatch.setattr(beep, "tempfile", tempfile, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    class Process:
        def wait(self, timeout=None):
            return 0

    def popen(args, **kwargs):
        seen.append(args)
        with wave.open(args[-1], "rb") as sound:
            frames = sound.readframes(sound.getnframes())
        assert frames[:8000] == b"\0" * 8000
        assert frames[8000:] == b"\x01\x02" * 160
        return Process()

    monkeypatch.setattr(beep.subprocess, "Popen", popen)
    beep.start()
    assert seen and seen[0][0] == "pw-play"
    assert seen[0][-1] != str(tone)
    for _ in range(50):
        if not os.path.exists(seen[0][-1]):
            break
        time.sleep(0.01)
    assert not os.path.exists(seen[0][-1])


def test_non_bluetooth_start_tone_is_unchanged(tmp_path, monkeypatch):
    tone = tmp_path / "start.wav"
    _tone(tone)
    calls = []
    monkeypatch.setattr(beep, "START_WAV", str(tone))
    monkeypatch.setattr(beep.sys, "platform", "linux")
    monkeypatch.setattr(beep.os, "name", "posix")
    monkeypatch.setattr(beep.shutil, "which", lambda name: name == "pw-play")
    monkeypatch.setattr(beep.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="alsa_output.internal\n"))
    monkeypatch.setattr(beep.subprocess, "Popen",
                        lambda args, **kw: calls.append(args) or SimpleNamespace())
    beep.start()
    assert calls == [["pw-play", str(tone)]]
