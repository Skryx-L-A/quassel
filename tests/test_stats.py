"""Tests der lokalen Nutzungsstatistik (Issue #22).

Der Speicherpfad wird ueber QUASSEL_STATS_PATH auf eine temporaere Datei
umgebogen, sodass das echte Datenverzeichnis nie angefasst wird."""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Pfad auf eine eindeutige temporaere Datei legen, BEVOR stats importiert wird.
_fd, _TMP = tempfile.mkstemp(prefix="quassel_stats_", suffix=".json")
os.close(_fd)
os.remove(_TMP)  # nur den Namen behalten; record() legt die Datei selbst an
os.environ["QUASSEL_STATS_PATH"] = _TMP

from quassel import stats


def test_empty_summary_is_zero():
    assert not os.path.exists(_TMP)
    s = stats.summary()
    assert s["sessions"] == 0
    assert s["words"] == 0
    assert s["chars"] == 0
    assert s["seconds_saved"] == 0.0
    assert s["first_ts"] is None
    assert s["last_ts"] is None


def test_record_accumulates():
    stats.record("hello world")            # 2 Woerter, 11 Zeichen
    stats.record("three more words here")  # 4 Woerter, 21 Zeichen
    s = stats.summary()
    assert s["words"] == 6
    assert s["sessions"] == 2
    assert s["chars"] == len("hello world") + len("three more words here")
    assert s["seconds_saved"] > 0
    assert s["seconds_saved"] == 6 / 40 * 60
    assert s["first_ts"] is not None
    assert s["last_ts"] is not None
    assert s["last_ts"] >= s["first_ts"]


def test_format_summary():
    out = stats.format_summary()
    assert isinstance(out, str)
    assert "words" in out
    assert "sessions" in out
    # auch mit explizitem dict aufrufbar
    out2 = stats.format_summary({"words": 1234, "sessions": 56, "seconds_saved": 1860.0})
    assert "1,234 words in 56 sessions" in out2


def test_daily_and_spoken():
    stats.reset()
    stats.record("eins zwei drei", seconds=3.0)     # 3 Woerter, 3 s gesprochen
    s = stats.summary()
    assert s["seconds_spoken"] == 3.0, s["seconds_spoken"]
    assert stats.words_today(s) == 3, stats.words_today(s)
    series = stats.daily_series(7, s)
    assert len(series) == 7
    assert series[-1][1] == 3, series          # heute
    assert series[0][1] == 0, series           # vor 6 Tagen
    stats.reset()


def test_reset_clears():
    stats.reset()
    assert not os.path.exists(_TMP)
    s = stats.summary()
    assert s["sessions"] == 0
    assert s["words"] == 0
    assert s["chars"] == 0
    assert s["seconds_saved"] == 0.0
    assert s["first_ts"] is None
    assert s["last_ts"] is None


if __name__ == "__main__":
    try:
        for fn in [test_empty_summary_is_zero, test_record_accumulates,
                   test_format_summary, test_daily_and_spoken, test_reset_clears]:
            fn()
    finally:
        try:
            os.remove(_TMP)
        except OSError:
            pass
    print("ok")
