"""Qt-Pille (macOS/Windows): der Transparenz-Regler färbt nur den ovalen
Hintergrund (auch nach einer Konfigänderung zur Laufzeit) — die Sprachbalken
bleiben immer voll deckend. Außerdem lässt sich die Pille per Ziehen
verschieben, wenn `pill.movable` an ist — offscreen, ohne echten Bildschirm
und ohne die echte Konfigurationsdatei anzufassen (isolated_cfg)."""
import configparser
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QPoint, QPointF         # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

from quassel import config, pill_qt                    # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_cfg(tmp_path, monkeypatch):
    """Jeder Test bekommt eine eigene config.ini — nie die echte anfassen."""
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))


@pytest.fixture
def pill(qapp, isolated_cfg):
    p = pill_qt.Pill()
    yield p
    p.close()


class FakeMouseEvent:
    """Duck-typed Ersatz für QMouseEvent — die Handler benutzen nur .button()
    und .globalPosition().toPoint()."""

    def __init__(self, button, gx, gy):
        self._button = button
        self._pos = QPointF(gx, gy)

    def button(self):
        return self._button

    def globalPosition(self):
        return self._pos


# ------------------------------------------------------------- Transparenz
@pytest.mark.parametrize("raw,expected", [(0.0, 0.15), (0.5, 0.5), (5.0, 1.0)])
def test_op_clamps_to_valid_range(pill, raw, expected):
    pill.cfg.pill_opacity = raw
    assert pill._op() == pytest.approx(expected)


def test_window_stays_fully_opaque(pill):
    # Nur das Oval wird transparent — das Fenster selbst (und damit alles,
    # was ohne eigene Alpha darauf gezeichnet wird) bleibt bei 1.0.
    pill.cfg.pill_opacity = 0.3
    assert pill.windowOpacity() == pytest.approx(1.0)


def test_bg_color_alpha_follows_opacity_setting(pill):
    pill.cfg.pill_opacity = 0.3
    # QColor quantisiert Alpha auf 1/255 — daher eine absolute statt der
    # relativen Standardtoleranz.
    assert pill._bg_color().alphaF() == pytest.approx(0.3, abs=0.01)


def test_wave_color_has_no_alpha_regardless_of_opacity(pill):
    pill.cfg.pill_opacity = 0.15
    assert pill._wave_color().alphaF() == pytest.approx(1.0)


def test_reload_cfg_applies_new_opacity_and_repaints(pill, monkeypatch):
    monkeypatch.setattr(pill.cfg, "reload", lambda: True)
    pill.cfg.pill_opacity = 0.3
    updated = []
    monkeypatch.setattr(pill, "update", lambda: updated.append(1))
    pill.reload_cfg()
    # QColor quantisiert Alpha auf 1/255 — daher eine absolute statt der
    # relativen Standardtoleranz.
    assert pill._bg_color().alphaF() == pytest.approx(0.3, abs=0.01)
    assert updated == [1]


# --------------------------------------------------------------- Verschieben
def test_click_without_movement_opens_center(pill, monkeypatch):
    pill.cfg.pill_movable = True
    calls = []
    monkeypatch.setattr(pill_qt.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    pill.mousePressEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    pill.mouseReleaseEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    assert len(calls) == 1


def test_drag_past_threshold_moves_saves_and_skips_center(pill, monkeypatch):
    pill.cfg.pill_movable = True
    pill.move(50, 50)
    calls = []
    monkeypatch.setattr(pill_qt.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    pill.mousePressEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    pill.mouseMoveEvent(FakeMouseEvent(Qt.LeftButton, 215, 200))  # dx=15 > 4px-Schwelle
    assert pill.cursor().shape() == Qt.ClosedHandCursor
    pill.mouseReleaseEvent(FakeMouseEvent(Qt.LeftButton, 215, 200))
    assert pill.pos() == QPoint(65, 50)
    assert calls == []                          # kein Kontrollzentrum
    assert pill.cfg.pill_pos_x == 65
    assert pill.cfg.pill_pos_y == 50
    saved = configparser.ConfigParser()
    saved.read(config.CONFIG)
    assert saved.getint("pill", "pos_x") == 65
    assert saved.getint("pill", "pos_y") == 50


def test_drag_below_threshold_does_not_move(pill):
    pill.cfg.pill_movable = True
    pill.move(50, 50)
    pill.mousePressEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    pill.mouseMoveEvent(FakeMouseEvent(Qt.LeftButton, 202, 201))  # 2px, 1px
    assert pill.pos() == QPoint(50, 50)
    assert pill._dragging is False


def test_click_opens_center_by_default(pill, monkeypatch):
    calls = []
    monkeypatch.setattr(pill_qt.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    pill.mousePressEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    pill.mouseReleaseEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    assert len(calls) == 1


def test_click_does_not_open_center_when_disabled(pill, monkeypatch):
    pill.cfg.pill_click_opens_center = False
    calls = []
    monkeypatch.setattr(pill_qt.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    pill.mousePressEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    pill.mouseReleaseEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    assert calls == []


def test_right_click_still_toggles_when_click_center_disabled(pill, monkeypatch):
    pill.cfg.pill_click_opens_center = False
    toggled = []
    monkeypatch.setattr(pill, "_toggle", lambda: toggled.append(1))
    pill.mouseReleaseEvent(FakeMouseEvent(Qt.RightButton, 200, 200))
    assert toggled == [1]


def test_drag_does_nothing_when_not_movable(pill, monkeypatch):
    pill.cfg.pill_movable = False
    pill.move(50, 50)
    calls = []
    monkeypatch.setattr(pill_qt.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    pill.mousePressEvent(FakeMouseEvent(Qt.LeftButton, 200, 200))
    pill.mouseMoveEvent(FakeMouseEvent(Qt.LeftButton, 260, 260))
    assert pill.pos() == QPoint(50, 50)
    pill.mouseReleaseEvent(FakeMouseEvent(Qt.LeftButton, 260, 260))
    assert len(calls) == 1                      # normaler Klick öffnet weiterhin


def test_cursor_open_hand_when_movable(pill):
    pill.cfg.pill_movable = True
    pill._update_cursor()
    assert pill.cursor().shape() == Qt.OpenHandCursor


def test_cursor_arrow_when_not_movable(pill):
    pill.cfg.pill_movable = False
    pill._update_cursor()
    assert pill.cursor().shape() == Qt.ArrowCursor


# --------------------------------------------------------------- Position
def test_stored_position_used_when_on_screen(pill):
    pill.cfg.pill_movable = True
    g = QApplication.primaryScreen().availableGeometry()
    x, y = g.x() + 10, g.y() + 10
    pill.cfg.pill_pos_x, pill.cfg.pill_pos_y = x, y
    pill.reposition()
    assert pill.pos() == QPoint(x, y)


def test_stored_position_off_screen_falls_back_to_auto(pill):
    pill.cfg.pill_movable = True
    pill.cfg.pill_pos_x, pill.cfg.pill_pos_y = 999999, 999999
    pill.reposition()
    g = QApplication.primaryScreen().availableGeometry()
    expected_x = g.x() + (g.width() - pill.width()) // 2
    assert pill.pos().x() == expected_x


def test_movable_off_ignores_stored_position(pill):
    pill.cfg.pill_movable = False
    g = QApplication.primaryScreen().availableGeometry()
    pill.cfg.pill_pos_x, pill.cfg.pill_pos_y = g.x() + 10, g.y() + 10
    pill.reposition()
    assert pill.pos() != QPoint(g.x() + 10, g.y() + 10)


def test_resize_keeps_stored_position(pill):
    """Größenänderung über den Regler darf eine gespeicherte Position nicht
    verwerfen — die Prüfung hängt nur am Punkt, nicht an der Fenstergröße."""
    pill.cfg.pill_movable = True
    g = QApplication.primaryScreen().availableGeometry()
    x, y = g.x() + 5, g.y() + 5
    pill.cfg.pill_pos_x, pill.cfg.pill_pos_y = x, y
    pill.cfg.pill_scale = 1.5
    pill.resize_to_cfg()
    assert pill.pos() == QPoint(x, y)


def test_disabling_movable_restores_auto_position_on_reload(pill, monkeypatch):
    g = QApplication.primaryScreen().availableGeometry()
    pill.cfg.pill_movable = True
    pill.cfg.pill_pos_x, pill.cfg.pill_pos_y = g.x() + 10, g.y() + 10
    pill.reposition()
    assert pill.pos() == QPoint(g.x() + 10, g.y() + 10)

    monkeypatch.setattr(pill.cfg, "reload", lambda: True)
    pill.cfg.pill_movable = False
    pill.reload_cfg()
    expected_x = g.x() + (g.width() - pill.width()) // 2
    assert pill.pos().x() == expected_x
