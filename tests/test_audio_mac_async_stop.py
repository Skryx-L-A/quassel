"""macOS: das Diktat wartet nicht mehr auf das Schließen des Aufnahme-Streams.

Live-Log der installierten App (09.10.): in 28 von 1677 Diktaten hing das
Beenden des CoreAudio-Streams bis zur Frist von 2,0 s ("reagiert nicht ->
aufgegeben"), und jedes dieser Diktate kam 2 s später. Die Rohdatei war da
längst vollständig. Jetzt gibt stop() nach dem Schreiben zurück; das Schließen
läuft im Hintergrund, und erst die Prüfung auf einen aufgegebenen Stream (nach
dem Einfügen) oder der nächste Start wartet darauf.
"""
import time

from quassel import audio
from test_audio_mac import FakeSD, FakeStream, raws, recorder  # noqa: F401


def test_stop_returns_before_a_hanging_stream_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "MAC_STOP_TIMEOUT", 0.3)
    monkeypatch.setattr(audio, "_log", lambda m: None)
    st = FakeStream(hang={"abort"})
    monkeypatch.setattr(audio, "_sd", lambda: FakeSD(st))
    path = tmp_path / "rec.raw"
    ms = audio._MacStream(str(path))
    assert ms.start("default") is True
    st.callback(b"\x01\x02", 1, None, None)
    try:
        t0 = time.monotonic()
        ms.stop()
        assert time.monotonic() - t0 < 0.1
        assert path.read_bytes() == b"\x01\x02"
        st.callback(b"\x03\x04", 1, None, None)      # nach dem Stopp: nicht mehr
        assert ms.queue.qsize() == 0
        assert ms.wait_closed() is True             # Frist abgelaufen -> aufgegeben
    finally:
        st.release()


def test_a_normal_stream_closes_in_the_background(tmp_path, monkeypatch):
    st = FakeStream()
    monkeypatch.setattr(audio, "_sd", lambda: FakeSD(st))
    ms = audio._MacStream(str(tmp_path / "rec.raw"))
    assert ms.start("default") is True
    ms.stop()
    assert ms.wait_closed() is False
    assert st.calls == ["start", "abort", "close"]


def test_recorder_reports_the_hang_only_when_asked_after_the_insert(raws, monkeypatch):
    monkeypatch.setattr(audio, "MAC_STOP_TIMEOUT", 0.3)
    monkeypatch.setattr(audio, "_log", lambda m: None)
    st = FakeStream(hang={"abort"})
    rec = recorder(monkeypatch, st)
    assert rec.start() is True
    try:
        t0 = time.monotonic()
        rec.stop()
        assert time.monotonic() - t0 < 0.1
        assert rec.stream_abandoned is True          # wartet auf die Frist
    finally:
        st.release()


def test_next_start_waits_for_the_previous_stream_to_close(raws, monkeypatch):
    """PortAudio ist nicht threadsicher: ein neuer Stream darf erst geöffnet
    werden, wenn der alte geschlossen (oder aufgegeben) ist."""
    events = []

    class Slow(FakeStream):
        def close(self):
            time.sleep(0.15)
            events.append("old closed")
            super().close()

    old = Slow()
    rec = recorder(monkeypatch, old)
    assert rec.start() is True
    rec.stop()
    new = FakeStream()

    class SD(FakeSD):
        def RawInputStream(self, callback=None, **kw):
            events.append("new opened")
            return super().RawInputStream(callback=callback, **kw)
    monkeypatch.setattr(audio, "_sd", lambda: SD(new))
    assert rec.start() is True
    rec.stop()
    assert events[:2] == ["old closed", "new opened"]
