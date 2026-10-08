"""Windows-Pille (quassel/win/app.py) offscreen auf macOS/Linux: echter
Mikrofonpegel, Hinweis bei stummem Mikrofon, lesbare Textblase, Zustand für
Bildschirmleser. Kein Fenster wird sichtbar, keine echte Konfiguration."""
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication             # noqa: E402

from quassel import config, i18n, pill_qt, state      # noqa: E402
import test_win_stubs as win_stubs                     # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def app(monkeypatch, tmp_path, qapp):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(pill_qt, "reduced_motion", lambda: False)
    i18n.set_language("en")
    yield win_stubs.load_win_app(monkeypatch)
    i18n.set_language(None)


@pytest.fixture
def pill(app):
    p = app.Pill()
    yield p
    p.bubble.close()
    p.close()


def record(pill, level, age=0.0):
    pill.level_source = lambda: (level, time.time() - age)
    pill.set_mode("recording")
    pill._tick()


def test_windows_pill_shows_the_real_level(pill):
    record(pill, 0.3)
    fr = pill.body.wave_fracs()
    assert fr[-1] == pytest.approx(state.LevelMeter.to_frac(0.3))
    time.sleep(0.03)
    assert pill.body.wave_fracs() == fr            # keine Sinus-Bewegung mehr
    assert not pill.bubble.isVisible()


def test_windows_pill_flags_a_silent_microphone(pill):
    record(pill, 0.0)
    pill.body.meter.quiet_since -= 2.0
    pill._tick()
    assert pill.body.mic_silent()
    assert pill.body._wave_color() == pill_qt.WAVE_AMBER
    assert pill.bubble.label.text().startswith("No sound from the microphone")
    assert pill.bubble.isVisible()
    assert pill.accessibleDescription().startswith("Recording: No sound")
    record(pill, 0.4)                              # neues Diktat: Hinweis weg
    assert not pill.body.mic_silent()


def test_windows_pill_without_level_keeps_the_old_animation(pill):
    pill.level_source = lambda: None
    pill.set_mode("recording")
    pill._tick()
    assert pill.body.meter.seen is False and not pill.body.mic_silent()


def test_stale_level_counts_as_silence_after_it_was_seen(pill):
    record(pill, 0.3)
    pill.level_source = lambda: (0.3, time.time() - 5.0)   # Pumpe steht
    pill.body.meter.quiet_since -= 2.0
    pill._tick()
    assert pill.body.mic_silent()


def test_every_state_has_its_own_shape(pill):
    shapes = set()
    for mode in ("off", "ready", "transcribing", "done", "error"):
        pill.set_mode(mode, "x")
        shapes.add(tuple(pill.body.wave_fracs()))
    assert len(shapes) == 5


def test_bubble_stays_readable_on_a_transparent_pill(pill):
    pill.cfg.pill_opacity = 0.15
    bg = pill.bubble.bg_color()
    assert bg.alphaF() >= pill_qt.BUBBLE_MIN_ALPHA - 0.01
    assert (bg.red(), bg.green(), bg.blue()) == (17, 32, 26)     # Pillen-Token
    pill.set_mode("done", "Hallo")
    assert pill_qt.C_TEXT.name() in pill.bubble.label.styleSheet()


def test_pill_has_name_and_status_for_screen_readers(pill):
    assert pill.accessibleName() == "Quassel"
    pill.set_mode("error", "Server not reachable")
    assert pill.accessibleDescription() == "Error: Server not reachable"


def test_offscreen_render_silent_body_is_amber(pill):
    record(pill, 0.0)
    pill.body.meter.quiet_since -= 2.0
    img = pill.body.grab().toImage()
    amber = pill_qt.WAVE_AMBER
    hits = sum(1 for y in range(img.height()) for x in range(img.width())
               if abs(img.pixelColor(x, y).red() - amber.red()) < 40
               and abs(img.pixelColor(x, y).green() - amber.green()) < 40
               and abs(img.pixelColor(x, y).blue() - amber.blue()) < 40)
    assert hits > 0


def test_core_sends_the_level_to_the_app(app):
    core = app.WinDictation.__new__(app.WinDictation)
    got = []
    core.owner = SimpleNamespace(set_level=got.append)
    core._level_target()(0.5)
    assert got == [0.5]
    core.owner = SimpleNamespace()                 # ohne Pille: keine Pumpe
    assert core._level_target() is None


def test_app_keeps_level_with_a_timestamp(app):
    winapp = app.WinApp.__new__(app.WinApp)
    winapp.set_level(0.2)
    level, ts = winapp._level
    assert level == 0.2 and abs(ts - time.time()) < 1.0
