"""Tests des macOS-Aufnahme-Streams (_MacStream.stop) ohne PortAudio.

Die Stream-Attrappen bilden genau den Fall aus dem Nutzer-Log nach: ein
CoreAudio-Gerät, das nach einem Hardwarefehler nicht mehr antwortet, sodass
PortAudio beim Beenden nie zurückkehrt. Threads und Fristen sind echt.
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import audio


class FakeStream:
    """sounddevice-Stream-Double; die in `hang` genannten Aufrufe kehren
    nicht zurück, bis release() sie freigibt."""

    def __init__(self, hang=()):
        self.calls = []
        self.callback = None       # setzt FakeSD beim Öffnen des Streams
        self._hang = set(hang)
        self._block = threading.Event()

    def _do(self, name):
        self.calls.append(name)
        if name in self._hang:
            self._block.wait(30)

    def abort(self):
        self._do("abort")

    def stop(self):
        self._do("stop")

    def close(self):
        self._do("close")

    def start(self):
        self.calls.append("start")

    def release(self):
        self._block.set()


class FakeSD:
    """sounddevice-Ersatz: gibt den vorbereiteten Stream zurück und merkt sich
    den Audio-Callback, damit der Test ihn selbst auslösen kann. Es wird kein
    echtes Gerät geöffnet."""

    def __init__(self, stream):
        self.stream = stream

    def RawInputStream(self, callback=None, **_kw):
        self.stream.callback = callback
        return self.stream


class OldStream:
    """Ältere sounddevice-Version ohne abort()."""

    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")


def make(tmp_path, stream):
    ms = audio._MacStream(str(tmp_path / "rec.raw"))
    ms.stream = stream
    return ms


@pytest.fixture
def raws(tmp_path, monkeypatch):
    """Beide Diktat-Rohdateien in ein Testverzeichnis legen. Ohne das schriebe
    der Test in das Laufzeitverzeichnis der laufenden Installation."""
    a, b = tmp_path / "rec.raw", tmp_path / "rec-b.raw"
    monkeypatch.setattr(audio, "RAW", str(a))
    monkeypatch.setattr(audio, "RAW_B", str(b))
    monkeypatch.setattr(audio, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    return a, b


def test_stop_prefers_abort_over_stop(tmp_path):
    """abort() (Pa_AbortStream) wartet nicht auf die Callback-Drainage —
    für einen reinen Eingabe-Stream gibt es nichts zu leeren."""
    st = FakeStream()
    ms = make(tmp_path, st)
    ms.stop()
    assert st.calls == ["abort", "close"]
    assert ms.stream is None


def test_stop_falls_back_to_stop_without_abort(tmp_path):
    """Ältere sounddevice-Versionen haben kein abort() — und manche Attrappen
    tragen es als None. Beide Fälle müssen über stop() gehen, statt zu werfen."""
    st = OldStream()
    ms = make(tmp_path, st)
    ms.stop()
    assert st.calls == ["stop", "close"]
    st2 = OldStream()
    st2.abort = None
    ms2 = make(tmp_path, st2)
    ms2.stop()
    assert st2.calls == ["stop", "close"]


def test_stop_gives_up_on_a_hanging_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "MAC_STOP_TIMEOUT", 0.2)
    logs = []
    monkeypatch.setattr(audio, "_log", logs.append)
    st = FakeStream(hang={"abort"})
    ms = make(tmp_path, st)
    t0 = time.monotonic()
    try:
        ms.stop()
        took = time.monotonic() - t0
        assert took < 2.0, took
        assert ms.stream is None                 # Referenz fallen gelassen
        assert any("aufgegeben" in m for m in logs), logs
    finally:
        st.release()


def test_stop_keeps_the_recording_even_if_the_stream_hangs(tmp_path, monkeypatch):
    """Aufgegebener Stream heißt nicht verlorenes Diktat: die Rohdatei wird
    sauber geschrieben und geschlossen."""
    monkeypatch.setattr(audio, "MAC_STOP_TIMEOUT", 0.2)
    monkeypatch.setattr(audio, "_log", lambda m: None)
    path = tmp_path / "rec.raw"
    ms = audio._MacStream(str(path))
    ms.outfile = open(path, "wb")
    ms.thread = threading.Thread(target=ms._writer, args=(None,), daemon=True)
    ms.thread.start()
    ms.queue.put(b"\x01\x02\x03\x04")
    st = FakeStream(hang={"abort"})
    ms.stream = st
    try:
        ms.stop()
        assert ms.outfile is None and ms.thread is None
        assert path.read_bytes() == b"\x01\x02\x03\x04"
    finally:
        st.release()


def test_abandoned_stream_stops_feeding_the_queue(tmp_path, monkeypatch):
    """Der Callback eines aufgegebenen Streams kann weiterlaufen, während der
    Writer-Thread längst beendet ist — was er einreiht, holt niemand mehr ab.
    Ohne Riegel wüchse die Queue unbegrenzt."""
    monkeypatch.setattr(audio, "MAC_STOP_TIMEOUT", 0.2)
    monkeypatch.setattr(audio, "_log", lambda m: None)
    st = FakeStream(hang={"abort"})
    monkeypatch.setattr(audio, "_sd", lambda: FakeSD(st))
    path = tmp_path / "rec.raw"
    ms = audio._MacStream(str(path))
    assert ms.start("default") is True
    st.callback(b"\x01\x02", 1, None, None)      # regulärer Block
    try:
        ms.stop()                                 # abort hängt -> aufgegeben
        assert ms._abandoned is True
        assert ms.queue.qsize() == 0
        st.callback(b"\x03\x04", 1, None, None)   # Callback des toten Streams
        assert ms.queue.qsize() == 0
        assert path.read_bytes() == b"\x01\x02"   # der reguläre Block ist da
    finally:
        st.release()


def test_running_stream_keeps_feeding_the_queue(tmp_path, monkeypatch):
    """Gegenprobe: ohne Aufgabe schreibt der Callback ganz normal weiter."""
    monkeypatch.setattr(audio, "_log", lambda m: None)
    st = FakeStream()
    monkeypatch.setattr(audio, "_sd", lambda: FakeSD(st))
    path = tmp_path / "rec.raw"
    ms = audio._MacStream(str(path))
    assert ms.start("default") is True
    st.callback(b"\x01\x02", 1, None, None)
    st.callback(b"\x03\x04", 1, None, None)
    ms.stop()
    assert ms._abandoned is False
    assert path.read_bytes() == b"\x01\x02\x03\x04"


def test_stop_is_idempotent(tmp_path):
    st = FakeStream()
    ms = make(tmp_path, st)
    ms.stop()
    ms.stop()                                    # zweiter Aufruf fasst nichts an
    assert st.calls == ["abort", "close"]


# ------------------------------------------- Doppelpuffer für die Rohdatei

def recorder(monkeypatch, stream):
    """Recorder auf dem macOS-Pfad, gegen ein sounddevice-Double."""
    monkeypatch.setattr(audio, "_sd", lambda: FakeSD(stream))
    return audio.Recorder(raw_path=audio.RAW)


def test_recorder_alternates_between_two_raw_files(raws, monkeypatch):
    a, b = raws
    rec = recorder(monkeypatch, FakeStream())
    assert rec.start() is True and rec.raw_path == str(a)
    rec.stop()
    assert rec.start() is True and rec.raw_path == str(b)
    rec.stop()
    assert rec.start() is True and rec.raw_path == str(a)
    rec.stop()


def test_new_recording_does_not_overwrite_the_previous_file(raws, monkeypatch):
    """Das Restfenster aus Runde 2: solange ein finish() seine Rohdatei noch
    nicht gelesen hat, darf das nächste rec.start() sie nicht kürzen."""
    a, b = raws
    st = FakeStream()
    rec = recorder(monkeypatch, st)
    assert rec.start() is True
    st.callback(b"\x01\x02\x03\x04", 1, None, None)
    rec.stop()
    assert a.read_bytes() == b"\x01\x02\x03\x04"
    assert rec.raw_bytes() == b"\x01\x02\x03\x04"
    assert rec.start() is True                       # nächstes Diktat
    try:
        assert rec.raw_path == str(b)
        assert a.read_bytes() == b"\x01\x02\x03\x04"  # Diktat 1 unversehrt
    finally:
        rec.stop()


def test_own_raw_path_never_alternates(raws, tmp_path, monkeypatch):
    """Der Wake-Listener (und der Mikrofontest) haben eine eigene Datei und
    bleiben bei genau dieser."""
    own = tmp_path / "wake.raw"
    monkeypatch.setattr(audio, "_sd", lambda: FakeSD(FakeStream()))
    rec = audio.Recorder(raw_path=str(own))
    for _ in range(3):
        assert rec.start() is True
        assert rec.raw_path == str(own)
        rec.stop()


def test_newest_raw_follows_the_last_written_file(raws):
    a, b = raws
    assert audio.newest_raw() == str(a)          # nichts da -> Vorgabe
    a.write_bytes(b"x")
    os.utime(a, (1000, 1000))
    b.write_bytes(b"y")
    os.utime(b, (2000, 2000))
    assert audio.newest_raw() == str(b)
    os.utime(a, (3000, 3000))
    assert audio.newest_raw() == str(a)


def test_recorder_reports_an_abandoned_stream(raws, monkeypatch):
    """Der Daemon startet sich danach neu — dafür muss das Aufgeben des Streams
    bis zum Recorder durchschlagen."""
    monkeypatch.setattr(audio, "MAC_STOP_TIMEOUT", 0.2)
    monkeypatch.setattr(audio, "_log", lambda m: None)
    st = FakeStream(hang={"abort"})
    rec = recorder(monkeypatch, st)
    assert rec.stream_abandoned is False
    assert rec.start() is True
    try:
        rec.stop()
        assert rec.stream_abandoned is True
    finally:
        st.release()


def test_stop_returns_the_closed_path_and_raw_bytes_honours_it(raws, monkeypatch):
    """stop() nennt die Datei, die es gerade geschlossen hat. Wer sie an
    raw_bytes() zurückgibt, liest sein eigenes Diktat — auch wenn längst das
    nächste auf der anderen Datei läuft."""
    a, b = raws
    st = FakeStream()
    rec = recorder(monkeypatch, st)
    assert rec.start() is True
    st.callback(b"\x01\x02\x03\x04", 1, None, None)
    done = rec.stop()
    assert done == str(a) and rec.last_path == str(a)
    assert rec.start() is True                  # nächstes Diktat, andere Datei
    try:
        assert rec.raw_path == str(b)
        assert rec.raw_bytes(done) == b"\x01\x02\x03\x04"
        assert rec.raw_bytes() != rec.raw_bytes(done)   # ohne Pfad: die laufende
    finally:
        rec.stop()


def test_stop_names_the_file_of_the_recording_it_started(raws, monkeypatch):
    """Die Zusicherung gehört der Klasse, nicht dem Aufrufer: stop() nennt die
    Datei, die beim START dieser Aufnahme festgelegt wurde — auch wenn
    raw_path inzwischen woanders hinzeigt."""
    a, b = raws
    st = FakeStream()
    rec = recorder(monkeypatch, st)
    assert rec.start() is True
    assert rec.running_path == str(a)
    rec.raw_path = str(b)                    # jemand hat inzwischen umgeschaltet
    assert rec.stop() == str(a)
    assert rec.last_path == str(a)
    assert rec.running_path is None          # nichts läuft mehr


def test_stop_returns_a_path_even_without_a_recording(tmp_path):
    """Gilt auf jedem Betriebssystem: stop() nennt immer eine Datei, auch wenn
    gar nichts lief (Linux und Windows verhalten sich unverändert)."""
    own = str(tmp_path / "wake.raw")
    rec = audio.Recorder(raw_path=own)
    assert rec.stop() == own
    assert rec.last_path == own


def test_healthy_stream_leaves_the_recorder_clean(raws, monkeypatch):
    rec = recorder(monkeypatch, FakeStream())
    assert rec.start() is True
    rec.stop()
    assert rec.stream_abandoned is False


def test_start_stops_a_predecessor_instead_of_leaking_it(raws, monkeypatch):
    """Eine Aufnahme, die niemand beendet hat (verlorenes stop(), Not-Aus
    mitten im Ablauf), nähme das Mikrofon mit und schriebe still weiter."""
    monkeypatch.setattr(audio, "_log", lambda m: None)
    st = FakeStream()
    rec = recorder(monkeypatch, st)
    assert rec.start() is True
    assert rec.start() is True                   # zweites Mal ohne stop()
    try:
        assert st.calls.count("abort") == 1      # der Vorgänger wurde beendet
        assert st.calls.count("close") == 1
    finally:
        rec.stop()


def test_two_threads_stopping_at_once_do_not_collide(raws, monkeypatch):
    """Befund des Reviews: der Not-Aus fällt aus einem anderen Thread in ein
    laufendes stop() und griff dort auf ein schon abgeräumtes self.mac zu —
    gemessen als AttributeError im finish-Thread."""
    monkeypatch.setattr(audio, "MAC_STOP_TIMEOUT", 0.3)
    monkeypatch.setattr(audio, "_log", lambda m: None)
    st = FakeStream(hang={"abort"})
    rec = recorder(monkeypatch, st)
    assert rec.start() is True
    errors, paths = [], []

    def stopper():
        try:
            paths.append(rec.stop())
        except Exception as exc:      # noqa: BLE001 — genau das darf nicht passieren
            errors.append(exc)

    threads = [threading.Thread(target=stopper) for _ in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(3)
        assert errors == [], errors
        assert st.calls.count("abort") == 1       # nur einer beendet den Stream
        assert paths == [str(raws[0]), str(raws[0])]
    finally:
        st.release()
