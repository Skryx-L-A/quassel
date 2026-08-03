"""Qt-Pille (macOS/Windows): der Transparenz-Regler färbt nur den ovalen
Hintergrund (auch nach einer Konfigänderung zur Laufzeit) — die Sprachbalken
bleiben immer voll deckend. Außerdem lässt sich die Pille per Ziehen
verschieben, wenn `pill.movable` an ist — offscreen, ohne echten Bildschirm
und ohne die echte Konfigurationsdatei anzufassen (isolated_cfg)."""
import configparser
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QPoint, QPointF         # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

from quassel import config, pill_qt, state             # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_cfg(tmp_path, monkeypatch):
    """Jeder Test bekommt eine eigene config.ini und ein eigenes RUNDIR — nie
    die echte Konfiguration oder das RUNDIR der laufenden Instanz anfassen
    (dort schreibt state.py, unter anderem der auf Port 8765 laufende Daemon).

    `pill_qt.RUNDIR` allein reicht dafür nicht: die Pille liest den Zustand
    über `state_read()`, und das öffnet `state.STATE` — eine eigene
    Modulkonstante, die von `pill_qt.RUNDIR` nichts weiß. Ohne diese zweite
    Umleitung liest jeder Test die state.json der laufenden Instanz mit, und
    ein dort stehendes „done" versetzt die Pille mitten im Test in den
    Ergebnismodus (Timer-Test schlug genau dann fehl, wenn Quassel lief)."""
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(pill_qt, "RUNDIR", str(tmp_path / "run"))
    monkeypatch.setattr(state, "RUNDIR", str(tmp_path / "run"))
    monkeypatch.setattr(state, "STATE", str(tmp_path / "run" / "state.json"))


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


# ------------------------------------------------------------ Timer-Taktstufen
def test_timer_runs_slow_when_idle(pill):
    pill.set_mode("ready")
    assert pill.timer.interval() == pill_qt.TICK_SLOW_MS


def test_timer_speeds_up_while_recording(pill):
    pill.set_mode("recording")
    assert pill.timer.interval() == pill_qt.TICK_FAST_MS


def test_timer_stays_fast_during_result_window(pill):
    pill.set_mode("done", "hallo")
    assert pill.timer.interval() == pill_qt.TICK_FAST_MS


def test_timer_slows_down_once_result_window_expires(pill):
    pill.set_mode("done", "hallo")
    pill.result_until = time.monotonic() - 0.1   # Fenster bereits abgelaufen
    pill.tick()
    assert pill.timer.interval() == pill_qt.TICK_SLOW_MS


def test_timer_stops_while_pill_disabled(pill):
    pill.cfg.pill_enabled = False
    pill.reload_cfg()
    assert pill.timer.isActive() is False


def test_timer_restarts_when_pill_reenabled(pill, monkeypatch):
    pill.cfg.pill_enabled = False
    pill.reload_cfg()
    assert pill.timer.isActive() is False
    monkeypatch.setattr(pill.cfg, "reload", lambda: True)
    pill.cfg.pill_enabled = True
    pill.reload_cfg()
    assert pill.timer.isActive() is True


def test_cfg_timer_keeps_running_while_pill_disabled(pill):
    pill.cfg.pill_enabled = False
    pill.reload_cfg()
    assert pill.cfg_timer.isActive() is True


# ---------------------------------------------------------- daemon_active-Cache
def test_daemon_active_caches_systemctl_result(monkeypatch):
    monkeypatch.setattr(pill_qt.sys, "platform", "linux")
    monkeypatch.setattr(pill_qt, "_daemon_active_cache",
                        {"ts": float("-inf"), "val": None})
    calls = []

    def fake_run(*a, **kw):
        calls.append(1)
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(pill_qt.subprocess, "run", fake_run)
    t = [1000.0]
    monkeypatch.setattr(pill_qt.time, "monotonic", lambda: t[0])
    assert pill_qt.daemon_active() is True
    assert pill_qt.daemon_active() is True     # aus dem Cache, kein neuer Aufruf
    assert len(calls) == 1
    t[0] += pill_qt.DAEMON_ACTIVE_CACHE_S + 1   # Cache abgelaufen
    assert pill_qt.daemon_active() is True
    assert len(calls) == 2


def test_toggle_invalidates_cache_so_the_next_poll_does_not_revert_the_mode(pill, monkeypatch):
    """Regression: _toggle() schaltete self.on/mode von Hand, ließ den
    10s-Cache aber auf dem alten Wert stehen. tick() sah beim nächsten Poll
    on != self.on und drehte den gerade gesetzten Modus zurück -- bis zu 10s
    zeigte die Pille das Gegenteil des geschalteten Zustands."""
    monkeypatch.setattr(pill_qt.sys, "platform", "linux")

    def fake_run(args, **kw):
        if "is-active" in args:
            return SimpleNamespace(returncode=1)   # nach dem Stop: inaktiv
        return SimpleNamespace(returncode=0)        # stop/start-Aufruf selbst
    monkeypatch.setattr(pill_qt.subprocess, "run", fake_run)
    # Cache noch warm vom letzten Poll VOR dem Umschalten (daemon war an)
    monkeypatch.setattr(pill_qt, "_daemon_active_cache",
                        {"ts": time.monotonic(), "val": True})
    pill.on = True
    pill.mode = "ready"

    pill._toggle()                     # schaltet ab
    assert pill.mode == "off"

    pill.tick()                        # nächster Poll -- darf NICHT zurückdrehen
    assert pill.mode == "off", "tick() hat den Modus nach dem manuellen Toggle zurückgedreht"


# --------------------------------------------------- state.json-Watcher (RUNDIR)
def test_directory_watcher_triggers_an_immediate_tick(pill, monkeypatch):
    """Ohne echtes Dateisystem-Ereignis: das Signal direkt auslösen und
    prüfen, dass dieselbe tick()-Logik greift (hier: eine neue state.json
    landet sofort im Modus, statt erst am nächsten Timer-Takt)."""
    ticked = []
    monkeypatch.setattr(pill, "tick", lambda: ticked.append(1))
    pill._fs_watcher.directoryChanged.emit(pill_qt.RUNDIR)
    assert ticked == [1]


def test_timer_keeps_running_alongside_the_watcher(pill):
    """Der Timer bleibt Sicherheitsnetz -- der Watcher ersetzt ihn nicht."""
    assert pill.timer.isActive() is True
    assert pill_qt.RUNDIR in pill._fs_watcher.directories()


def test_tick_reads_the_isolated_state_file_not_the_running_instance(pill, tmp_path):
    """Regression zur Testisolation: tick() muss die state.json aus dem
    umgeleiteten RUNDIR lesen. Fehlt die Umleitung von `state.STATE` in
    isolated_cfg, liest der Test stattdessen die Datei der laufenden Instanz
    -- dann steht hier ein fremder Text und andere Tests kippen je nachdem,
    ob Quassel gerade läuft und was zuletzt diktiert wurde."""
    rundir = tmp_path / "run"
    rundir.mkdir(exist_ok=True)
    (rundir / "state.json").write_text(
        '{"state": "done", "text": "isolierter Testtext", "ts": 12345.0}',
        encoding="utf-8")
    pill.on = True
    pill.tick()
    assert pill.mode == "done"
    assert pill.text == "isolierter Testtext"
