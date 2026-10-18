"""Opt-in pre-roll uses in-memory PCM only; no audio device is opened."""
import threading
import time

from quassel import audio, coreaudio


def test_ring_keeps_only_requested_audio_and_prepends_it(tmp_path):
    capture = audio._PrerollCapture("default", 100)  # 3200 bytes
    capture.thread = threading.Thread(target=capture._write, daemon=True)
    capture.thread.start()
    try:
        capture.queue.put(b"a" * 2000)
        capture.queue.put(b"b" * 2000)
        path = tmp_path / "rec.raw"
        assert capture.begin(path)
        capture.queue.put(b"c" * 100)
        capture.end()
        assert path.read_bytes() == b"a" * 1200 + b"b" * 2000 + b"c" * 100
    finally:
        capture.queue.put(None)
        capture.thread.join(timeout=1)


def test_recorder_reuses_warm_capture_then_expires(tmp_path, monkeypatch):
    calls = []

    class Capture:
        def __init__(self, mic, ms):
            self.mic = mic
            self.abandoned = False
            calls.append(("new", ms))

        def open(self):
            calls.append(("open",))
            return True

        def begin(self, path):
            calls.append(("begin", path))
            return True

        def end(self):
            calls.append(("end",))

        def close(self):
            calls.append(("close",))

        def device_changed(self):
            return False

    monkeypatch.setattr(audio, "_PrerollCapture", Capture)
    monkeypatch.setattr(audio, "RUNDIR", str(tmp_path))
    rec = audio.Recorder(str(tmp_path / "rec.raw"), preroll_ms=300,
                         preroll_keep_s=0.05)
    assert rec.start()
    assert rec.active
    rec.stop()
    assert not rec.active
    assert rec.start()
    rec.stop()
    assert calls.count(("open",)) == 1
    deadline = time.monotonic() + 1
    while ("close",) not in calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ("close",) in calls


def test_warm_mac_capture_reopens_after_default_input_change(monkeypatch):
    selected = [10]
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.setattr(audio, "IS_MAC", True)
    monkeypatch.setattr(coreaudio, "default_input_device", lambda: selected[0])
    capture = audio._PrerollCapture("default", 300)
    capture.device_id = 10
    assert not capture.device_changed()
    selected[0] = 20
    assert capture.device_changed()
