"""Tests für den Windows-Recorder (quassel/win/audio_win.py) ohne Mikrofon.

sounddevice wird durch eine Attrappe ersetzt, deren Stream den Callback nur
auf Zuruf füttert. Alle Dateien liegen in tmp_path — nie im Laufzeit-
verzeichnis einer installierten App (Audit B26/B27)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel.win import audio_win


class FakeStream:
    def __init__(self, callback=None, **_kw):
        self.callback = callback
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        pass

    def feed(self, data):
        self.callback(data, len(data) // 2, None, None)


class FakeSD:
    def __init__(self, fail=False):
        self.streams = []
        self.fail = fail

    def RawInputStream(self, **kw):           # noqa: N802 — Name wie sounddevice
        if self.fail:
            raise OSError("kein Mikrofon")
        stream = FakeStream(**kw)
        stream.device = kw.get("device")
        self.streams.append(stream)
        return stream

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "Windows WASAPI"}]

    def query_devices(self):
        return [{"name": "Headset", "hostapi": 0, "max_input_channels": 1},
                {"name": "Headset", "hostapi": 1, "max_input_channels": 1},
                {"name": "Other", "hostapi": 1, "max_input_channels": 0}]


@pytest.fixture
def paths(tmp_path, monkeypatch):
    raw, raw_b = str(tmp_path / "rec.raw"), str(tmp_path / "rec-b.raw")
    monkeypatch.setattr(audio_win, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(audio_win, "RAW", raw)
    monkeypatch.setattr(audio_win, "RAW_B", raw_b)
    sd = FakeSD()
    monkeypatch.setattr(audio_win, "sd", sd)
    return raw, raw_b, sd


def test_dictations_alternate_between_two_raw_files(paths):
    raw, raw_b, _sd = paths
    rec = audio_win.Recorder()
    assert rec.start()
    assert rec.stop() == raw
    assert rec.start()
    assert rec.stop() == raw_b
    assert rec.start()
    assert rec.stop() == raw


def test_a_new_dictation_does_not_truncate_the_previous_one(paths):
    """B27: ein Chord im Nachlauf startet die nächste Aufnahme. Die vorige
    Datei bleibt lesbar, und der alte Callback schreibt nie in die neue."""
    _raw, _raw_b, sd = paths
    rec = audio_win.Recorder()
    rec.start()
    sd.streams[0].feed(b"\x01\x00" * 1000)
    done = rec.stop()
    rec.start()                                   # nächstes Diktat
    sd.streams[1].feed(b"\x02\x00" * 10)
    sd.streams[0].feed(b"\x09\x00" * 10)          # verspäteter alter Callback
    assert rec.raw_bytes(done) == b"\x01\x00" * 1000
    assert rec.raw_bytes() == b"\x02\x00" * 10


def test_wake_recorder_has_its_own_file(paths, tmp_path):
    """B26: der Wake-Listener schreibt nie in die Diktat-Dateien."""
    raw, raw_b, _sd = paths
    wake_path = str(tmp_path / "wake.raw")
    wake = audio_win.Recorder(raw_path=wake_path)
    for _ in range(3):
        assert wake.start()
        assert wake.stop() == wake_path
    assert not os.path.exists(raw) and not os.path.exists(raw_b)


def test_failed_start_reports_false_and_keeps_nothing_open(paths, monkeypatch):
    monkeypatch.setattr(audio_win, "sd", FakeSD(fail=True))
    rec = audio_win.Recorder()
    assert rec.start() is False
    assert rec.stream is None and rec.outfile is None
    assert not rec.active


def test_raw_bytes_of_the_running_recording_flushes(paths):
    _raw, _raw_b, sd = paths
    rec = audio_win.Recorder()
    rec.start()
    sd.streams[0].feed(b"\x03\x00" * 50)
    assert rec.raw_bytes() == b"\x03\x00" * 50    # Live-Vorschau liest mit
    rec.stop()


def test_start_while_running_stops_the_previous_stream(paths):
    _raw, _raw_b, sd = paths
    rec = audio_win.Recorder()
    rec.start()
    rec.start()
    assert sd.streams[0].active is False
    assert sd.streams[1].active is True
    rec.stop()


def test_named_microphone_follows_current_wasapi_index(paths):
    _raw, _raw_b, sd = paths
    assert audio_win.list_mics() == [("Headset", "Headset")]
    rec = audio_win.Recorder()
    assert rec.start("Headset")
    assert sd.streams[-1].device == 1
    rec.stop()
    sd.query_devices = lambda: [{"name": "New", "hostapi": 1, "max_input_channels": 1},
                                {"name": "Headset", "hostapi": 0, "max_input_channels": 1},
                                {"name": "Headset", "hostapi": 1, "max_input_channels": 1}]
    assert audio_win.resolve_mic("Headset") == 2
    assert rec.start("Headset")
    assert sd.streams[-1].device == 2
    rec.stop()


def test_missing_named_microphone_fails_closed(paths):
    _raw, _raw_b, sd = paths
    rec = audio_win.Recorder()
    assert rec.start("missing") is False
    assert not sd.streams
