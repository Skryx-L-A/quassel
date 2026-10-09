"""Nachlauf nach dem Loslassen: Ereignis statt fester Wartezeit (Audit C43).

Bisher lief die Aufnahme nach dem Loslassen immer 250 ms weiter (Bluetooth
450 ms), auch wenn das letzte Wort längst zu Ende war. Jetzt endet der
Nachlauf, sobald das zuletzt angekommene Audio still ist, frühestens nach einer
Mindestzeit für die Gerätelatenz; die alte Länge bleibt die Obergrenze.
"""
from quassel import dictation


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


def _wait(levels, pad_ms=250, min_ms=80):
    clock = Clock()

    def level():
        return levels(clock.t) if callable(levels) else levels
    waited = dictation.wait_tail(pad_ms, min_ms, level, now=clock.now, sleep=clock.sleep)
    return round(clock.t * 1000), waited


def test_quiet_audio_ends_the_tail_after_the_minimum_and_a_short_hold():
    assert _wait(0.0)[0] == 80 + 40


def test_a_short_dip_inside_the_last_word_does_not_end_the_tail():
    """Replay 09.10., de-kurz-03: das Wortende klingt mit leisen Reibelauten um
    die Schwelle aus; ein einzelner stiller Messpunkt schnitt 36 ms ab."""
    dip = lambda t: 0.01 if 0.10 <= t < 0.12 else (0.4 if t < 0.18 else 0.01)
    elapsed, _ = _wait(dip)
    assert elapsed >= 180 + 40


def test_speech_keeps_recording_up_to_the_old_length():
    assert _wait(0.5)[0] == 250


def test_the_tail_ends_once_the_last_word_is_over():
    elapsed, _ = _wait(lambda t: 0.4 if t < 0.15 else 0.01)
    assert 190 <= elapsed <= 210


def test_without_a_level_the_full_tail_is_kept():
    assert _wait(None)[0] == 250


def test_bluetooth_minimum_and_a_zero_tail():
    assert _wait(0.0, pad_ms=450, min_ms=250)[0] == 290
    assert _wait(0.5, pad_ms=0, min_ms=80)[0] == 0
    assert _wait(0.0, pad_ms=50, min_ms=80)[0] == 50


def test_a_failing_level_reader_keeps_the_full_tail():
    def broken(t):
        raise OSError("weg")
    assert _wait(broken)[0] == 250


def test_returns_the_waited_milliseconds():
    assert _wait(0.0)[1] == 120
