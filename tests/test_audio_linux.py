"""Linux capture ordering without opening any audio device."""
import threading

from quassel import audio


def test_capture_starts_before_profile_query(tmp_path, monkeypatch):
    waiting = threading.Event()
    release = threading.Event()
    started = []

    class Process:
        def poll(self):
            return None

        def send_signal(self, _signal):
            pass

        def wait(self, timeout=None):
            return 0

    def profiles():
        waiting.set()
        assert release.wait(2)
        return {}

    def popen(*_args, **_kwargs):
        started.append(True)
        return Process()

    monkeypatch.setattr(audio, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(audio.sys, "platform", "linux")
    monkeypatch.setattr(audio, "IS_MAC", False)
    monkeypatch.setattr(audio, "record_command", lambda mic: ["pw-record", "-"])
    monkeypatch.setattr(audio, "_bluez_profiles", profiles)
    monkeypatch.setattr(audio.subprocess, "Popen", popen)
    rec = audio.Recorder(str(tmp_path / "rec.raw"))
    try:
        assert rec.start()
        assert started and waiting.wait(1)
    finally:
        release.set()
        rec.stop()
