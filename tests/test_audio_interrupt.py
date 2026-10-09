"""Abgerissene Aufnahme erkennen (D65), ohne ein Audiogerät zu öffnen.

Gerät abgezogen, Bluetooth getrennt oder Stromereignis: der Aufnahmeprozess
endet bzw. der Stream liefert keine Blöcke mehr. Der Recorder muss das bis
stop() merken (Recorder.interrupted) und auch mitten in der Aufnahme sagen
können (capture_interrupted), statt still eine leere Aufnahme zu liefern."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import audio


@pytest.fixture
def raws(tmp_path, monkeypatch):
    a, b = tmp_path / "rec.raw", tmp_path / "rec-b.raw"
    monkeypatch.setattr(audio, "RAW", str(a))
    monkeypatch.setattr(audio, "RAW_B", str(b))
    monkeypatch.setattr(audio, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(audio, "_log", lambda m: None)
    monkeypatch.delenv("QUASSEL_PREROLL_MS", raising=False)
    return a, b


# ------------------------------------------------------------ macOS-Stream
class Stream:
    def __init__(self):
        self.callback = None
        self.active = True

    def start(self):
        pass

    def abort(self):
        pass

    def close(self):
        pass


class SD:
    def __init__(self, stream):
        self.stream = stream

    def RawInputStream(self, callback=None, **_kw):
        self.stream.callback = callback
        return self.stream


def mac_recorder(monkeypatch, stream):
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    monkeypatch.setattr(audio, "_sd", lambda: SD(stream))
    monkeypatch.setattr(audio, "_mac_device", lambda mic: None)
    return audio.Recorder(raw_path=audio.RAW)


def block(stream):
    stream.callback(b"\0\0" * 160, 160, None, None)


def test_mac_healthy_recording_is_not_interrupted(raws, monkeypatch):
    st = Stream()
    rec = mac_recorder(monkeypatch, st)
    assert rec.start()
    block(st)
    assert rec.capture_interrupted() is None
    rec.stop()
    assert rec.interrupted is None


def test_mac_blocks_stop_coming_is_reported(raws, monkeypatch):
    monkeypatch.setattr(audio, "CAPTURE_STALL_S", 0.05)
    st = Stream()
    rec = mac_recorder(monkeypatch, st)
    assert rec.start()
    block(st)
    time.sleep(0.15)
    assert rec.capture_interrupted() == "stalled"
    rec.stop()
    assert rec.interrupted == "stalled"


def test_mac_no_first_block_counts_only_after_the_start_grace(raws, monkeypatch):
    monkeypatch.setattr(audio, "CAPTURE_STALL_S", 0.05)
    monkeypatch.setattr(audio, "CAPTURE_START_S", 0.1)
    st = Stream()
    rec = mac_recorder(monkeypatch, st)
    assert rec.start()
    time.sleep(0.07)
    assert rec.capture_interrupted() is None      # Bluetooth darf brauchen
    time.sleep(0.1)
    assert rec.capture_interrupted() == "stalled"
    rec.stop()


def test_mac_stream_ended_by_portaudio_is_reported(raws, monkeypatch):
    st = Stream()
    rec = mac_recorder(monkeypatch, st)
    assert rec.start()
    block(st)
    st.active = False
    rec.stop()
    assert rec.interrupted == "ended"


def test_next_recording_starts_clean(raws, monkeypatch):
    st = Stream()
    rec = mac_recorder(monkeypatch, st)
    assert rec.start()
    st.active = False
    rec.stop()
    assert rec.interrupted == "ended"
    st.active = True
    assert rec.start()
    block(st)
    rec.stop()
    assert rec.interrupted is None


# ------------------------------------------------------- Linux-Subprozess
class Proc:
    def __init__(self, outfile, exited=False):
        self.outfile = outfile
        self.exited = exited

    def poll(self):
        return 1 if self.exited else None

    def send_signal(self, _sig):
        self.exited = True

    def wait(self, timeout=None):
        return 0


def linux_recorder(monkeypatch, procs):
    monkeypatch.setattr(audio.sys, "platform", "linux")
    monkeypatch.setattr(audio, "record_command", lambda mic: ["pw-record", "-"])
    monkeypatch.setattr(audio, "_bluez_profiles", lambda: {})

    def popen(_cmd, stdout=None, stderr=None):
        p = Proc(stdout)
        procs.append(p)
        return p
    monkeypatch.setattr(audio.subprocess, "Popen", popen)
    return audio.Recorder(raw_path=audio.RAW)


def test_linux_healthy_recording_is_not_interrupted(raws, monkeypatch):
    procs = []
    rec = linux_recorder(monkeypatch, procs)
    assert rec.start()
    procs[0].outfile.write(b"\1\0" * 1000)
    procs[0].outfile.flush()
    assert rec.capture_interrupted() is None
    rec.stop()
    assert rec.interrupted is None


def test_linux_recorder_process_that_died_is_reported(raws, monkeypatch):
    procs = []
    rec = linux_recorder(monkeypatch, procs)
    assert rec.start()
    procs[0].exited = True           # pw-record weg, z. B. Gerät entfernt
    assert rec.capture_interrupted() == "ended"
    rec.stop()
    assert rec.interrupted == "ended"


def test_linux_file_that_stopped_growing_is_reported(raws, monkeypatch):
    procs = []
    rec = linux_recorder(monkeypatch, procs)
    assert rec.start()
    procs[0].outfile.write(b"\1\0" * 1000)
    procs[0].outfile.flush()
    old = time.time() - 5
    os.utime(rec.running_path, (old, old))
    rec.stop()
    assert rec.interrupted == "stalled"


def test_linux_empty_file_counts_only_after_the_start_grace(raws, monkeypatch):
    procs = []
    rec = linux_recorder(monkeypatch, procs)
    assert rec.start()
    assert rec.capture_interrupted() is None
    assert rec.capture_interrupted(now=time.time() + audio.CAPTURE_START_S + 1) \
        == "stalled"
    rec.stop()


# ------------------------------------------------------------- Vorlauf
def test_preroll_capture_reports_a_dead_recorder_process():
    cap = audio._PrerollCapture("default", 100)
    cap.proc = Proc(None, exited=True)
    assert cap.interrupted() == "ended"


def test_preroll_capture_reports_missing_blocks():
    cap = audio._PrerollCapture("default", 100)
    cap.opened = time.monotonic()
    cap.last_data = time.monotonic() - audio.CAPTURE_STALL_S - 1
    assert cap.interrupted() == "stalled"
    cap.last_data = time.monotonic()
    assert cap.interrupted() is None


def test_interrupted_preroll_capture_is_not_kept_warm(raws, monkeypatch):
    closed = []

    class Capture:
        abandoned = False

        def __init__(self, mic, ms):
            self.mic = mic

        def open(self):
            return True

        def begin(self, path):
            return True

        def end(self):
            pass

        def device_changed(self):
            return False

        def interrupted(self):
            return "ended"

        def close(self):
            closed.append(True)

    monkeypatch.setattr(audio, "_PrerollCapture", Capture)
    rec = audio.Recorder(raw_path=audio.RAW, preroll_ms=200, preroll_keep_s=30)
    assert rec.start()
    rec.stop()
    assert rec.interrupted == "ended"
    deadline = time.monotonic() + 2
    while not closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert closed == [True]
