"""Review 2026-10-09, Befund 6: das Abräumen des warmen Vorlauf-Streams
schließt das Gerät außerhalb der Recorder-Sperre. close() wartet bis zu
~15 s; start(), stop() und der Not-Aus dürfen dahinter nicht hängen."""
from quassel import audio


class SlowCapture:
    def __init__(self, rec):
        self.rec = rec
        self.abandoned = True
        self.lock_free_during_close = None

    def close(self):
        got = self.rec._lock.acquire(blocking=False)
        if got:
            self.rec._lock.release()
        self.lock_free_during_close = got


def test_expire_warm_closes_outside_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "RUNDIR", str(tmp_path))
    rec = audio.Recorder(str(tmp_path / "rec.raw"), preroll_ms=300)
    capture = SlowCapture(rec)
    rec._warm = capture
    rec._expire_warm(capture)
    assert capture.lock_free_during_close is True
    assert rec._warm is None and rec._warm_timer is None
    assert rec._abandoned is True


def test_expire_warm_leaves_a_newer_capture_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "RUNDIR", str(tmp_path))
    rec = audio.Recorder(str(tmp_path / "rec.raw"), preroll_ms=300)
    old, new = SlowCapture(rec), SlowCapture(rec)
    rec._warm = new
    rec._expire_warm(old)
    assert old.lock_free_during_close is None and rec._warm is new
