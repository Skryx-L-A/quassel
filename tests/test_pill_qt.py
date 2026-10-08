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
    run = tmp_path / "run"
    monkeypatch.setattr(state, "RUNDIR", str(run))
    monkeypatch.setattr(state, "STATE", str(run / "state.json"))
    # Die drei übrigen Pfade zeigen heute niemanden an, weil pill_qt nur
    # state_read und RUNDIR importiert. Sie werden trotzdem mit umgeleitet:
    # greift die Pille eines Tages auf eine davon zu, zeigt die Fixture sonst
    # still auf die Aufnahmedateien der laufenden Instanz -- und dann wäre es
    # ein SCHREIBzugriff, nicht bloß ein Lesefehler wie oben.
    monkeypatch.setattr(state, "RAW", str(run / "rec.raw"))
    monkeypatch.setattr(state, "WAV", str(run / "rec.wav"))
    monkeypatch.setattr(state, "PARTWAV", str(run / "partial.wav"))


@pytest.fixture(autouse=True)
def normal_motion(monkeypatch):
    """Systemeinstellung „Bewegung reduzieren" nie echt abfragen."""
    monkeypatch.setattr(pill_qt, "_query_reduced_motion", lambda: False)
    monkeypatch.setattr(pill_qt, "_reduced_motion_cache",
                        {"ts": float("-inf"), "val": False})


@pytest.fixture(autouse=True)
def no_secure_input(monkeypatch):
    """Sichere Eingabe nie echt abfragen (Ergebnis hinge vom Testrechner ab)."""
    monkeypatch.setattr(pill_qt, "secure_input_enabled", lambda: False)


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


# ------------------------------------------- H112 echter Mikrofonpegel
@pytest.fixture
def fresh_level_state(monkeypatch):
    """Modulzustand von state.py (letztes Dokument, Drossel) je Test frisch."""
    monkeypatch.setattr(state, "_doc", {"state": "idle", "text": "", "ts": 0.0})
    monkeypatch.setattr(state, "_level_written", float("-inf"))


def test_level_set_only_while_recording(fresh_level_state):
    state.state_set("idle")
    assert state.level_set(0.5, now=1.0) is False
    assert "level" not in state.state_read()
    state.state_set("recording")
    assert state.level_set(0.5, now=2.0) is True
    st = state.state_read()
    assert st["state"] == "recording" and st["level"] == 0.5
    assert state.level_read(st) == 0.5


def test_level_set_is_throttled_and_keeps_state_timestamp(fresh_level_state):
    """Höchstens LEVEL_HZ Schreibvorgänge je Sekunde; 'ts' bleibt der
    Zeitpunkt des Zustandswechsels, sonst hielte die Pille jedes Pegel-Update
    für einen neuen Zustand."""
    state.state_set("recording")
    ts = state.state_read()["ts"]
    assert state.level_set(0.1, now=10.0) is True
    assert state.level_set(0.2, now=10.0 + 0.5 / state.LEVEL_HZ) is False
    assert state.level_set(0.3, now=10.0 + 1.01 / state.LEVEL_HZ) is True
    st = state.state_read()
    assert st["level"] == 0.3 and st["ts"] == ts


def test_level_write_is_atomic(fresh_level_state, tmp_path):
    state.state_set("recording")
    state.level_set(0.25, now=1.0)
    assert not os.path.exists(state.STATE + ".tmp")
    assert sorted(os.listdir(os.path.dirname(state.STATE))) == ["state.json"]


def test_preview_text_keeps_level_but_new_state_drops_it(fresh_level_state):
    state.state_set("recording")
    state.level_set(0.4, now=1.0)
    state.state_set("recording", "live text")
    assert state.state_read()["level"] == 0.4
    state.state_set("transcribing")
    assert "level" not in state.state_read()


def test_level_read_rejects_stale_or_missing():
    now = 1000.0
    assert state.level_read({"state": "recording"}, now=now) is None
    assert state.level_read({"level": 0.3, "level_ts": now - 0.2}, now=now) == 0.3
    assert state.level_read({"level": 0.3, "level_ts": now - 2.0}, now=now) is None
    assert state.level_read({"level": "x", "level_ts": now}, now=now) is None


def test_meter_waits_for_a_level_source():
    m = state.LevelMeter(now=0.0)
    m.feed(None, now=5.0)
    assert m.seen is False and m.silent(now=5.0) is False


def test_meter_reports_silence_after_one_and_a_half_seconds():
    m = state.LevelMeter(now=0.0)
    m.feed(0.0, now=0.1)
    assert m.silent(now=1.4) is False
    m.feed(0.0, now=1.5)
    assert m.silent(now=1.5) is True
    m.feed(0.2, now=1.6)                      # jemand spricht: Hinweis weg
    assert m.silent(now=1.6) is False


def test_meter_treats_a_lost_level_as_silence():
    m = state.LevelMeter(now=0.0)
    m.feed(0.2, now=0.0)
    for t in (0.5, 1.0, 1.6):
        m.feed(None, now=t)
    assert m.silent(now=1.6) is True


def test_meter_scale_speech_visible_noise_flat():
    to = state.LevelMeter.to_frac
    assert to(0.0) == 0.0
    assert to(state.LevelMeter.SILENT_LEVEL) == 0.0    # Digitalstille
    assert 0.5 < to(1000 / 8000) < 0.8                 # normale Sprache
    assert to(1.0) == pytest.approx(1.0, abs=0.01)
    assert to(0.05) < to(0.1) < to(0.5)


def test_meter_bars_follow_history():
    m = state.LevelMeter(now=0.0)
    for i, lv in enumerate([0.0, 0.01, 0.1, 0.5, 1.0]):
        m.feed(lv, now=i * 0.1)
    bars = m.bars()
    assert len(bars) == 5 and bars == sorted(bars)
    calm = m.bars(calm=True)
    assert calm[2] == max(calm)                        # ruhiges Profil


def test_rms_level_reads_fresh_audio_and_zero_when_stalled(tmp_path, monkeypatch):
    import struct
    from quassel import audio
    raw = tmp_path / "rec.raw"
    monkeypatch.setattr(audio, "RAW", str(raw))
    monkeypatch.setattr(audio, "RAW_B", str(tmp_path / "rec-b.raw"))
    raw.write_bytes(struct.pack("<4800h", *([4000, -4000] * 2400)))
    now = os.path.getmtime(raw)
    assert audio.rms_level(now=now) == pytest.approx(0.5, abs=0.01)
    # Stream hängt: die Datei wächst nicht mehr -> kein eingefrorener Ausschlag
    assert audio.rms_level(now=now + audio.LEVEL_STALE_S + 0.1) == 0.0


def test_level_pump_publishes_until_stopped():
    import threading
    from quassel import audio
    got = []
    done = threading.Event()

    def publish(v):
        got.append(v)
        if len(got) >= 3:
            done.set()
    pump = audio.LevelPump(publish, hz=200, level=lambda: 0.25).start()
    try:
        assert done.wait(2.0)
    finally:
        pump.stop()
    n = len(got)
    time.sleep(0.05)
    assert len(got) == n and set(got) == {0.25}
    assert pump._thread is None


def test_level_pump_stops_quietly_on_error():
    from quassel import audio

    def broken():
        raise OSError("weg")
    pump = audio.LevelPump(lambda v: None, hz=200, level=broken).start()
    pump._thread.join(timeout=2.0)
    assert not pump._thread.is_alive()
    pump.stop()


def _recording(pill, level=None, now=None):
    """Pille in die Aufnahme versetzen und optional einen Pegel einspeisen."""
    doc = {"state": "recording", "text": "", "ts": 123.0}
    if level is not None:
        doc["level"] = level
        doc["level_ts"] = time.time() if now is None else now
    os.makedirs(os.path.dirname(state.STATE), exist_ok=True)
    with open(state.STATE, "w", encoding="utf-8") as f:
        import json
        json.dump(doc, f)
    pill.on = True
    pill.tick()


def test_pill_shows_real_level_not_animation(pill):
    _recording(pill, level=0.3)
    first = pill.wave_fracs()
    time.sleep(0.03)
    assert pill.wave_fracs() == first          # keine Sinus-Bewegung
    assert first[-1] == pytest.approx(state.LevelMeter.to_frac(0.3))
    assert pill.bubble_text() == ""


def test_pill_without_level_source_keeps_animation(pill):
    _recording(pill)                              # älterer Daemon: kein Pegel
    assert pill.meter.seen is False
    assert not pill.mic_silent()


def test_pill_shows_flat_line_and_hint_when_mic_is_silent(pill, monkeypatch):
    from quassel import i18n
    i18n.set_language("en")
    try:
        _recording(pill, level=0.0)
        pill.meter.quiet_since -= 2.0             # 2 s ohne Ton
        assert pill.mic_silent()
        assert pill.wave_fracs() == [0.0] * 5
        assert pill.bubble_text().startswith("No sound from the microphone")
        assert pill._wave_color() == pill_qt.WAVE_AMBER
    finally:
        i18n.set_language(None)


def test_silent_hint_shows_even_without_preview(pill):
    pill.cfg.pill_preview = False
    _recording(pill, level=0.0)
    pill.meter.quiet_since -= 2.0
    assert pill.bubble_text() != ""


def test_new_recording_resets_the_meter(pill):
    _recording(pill, level=0.0)
    pill.meter.quiet_since -= 2.0
    pill.set_mode("transcribing")
    pill.set_mode("recording")
    assert pill.meter.seen is False and not pill.mic_silent()


def _amber_pixels(img):
    amber = pill_qt.WAVE_AMBER
    hits = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if abs(c.red() - amber.red()) < 40 and abs(c.green() - amber.green()) < 40 \
                    and abs(c.blue() - amber.blue()) < 40:
                hits += 1
    return hits


def test_offscreen_render_silent_pill_is_amber_with_text(pill):
    """Sichtprüfung ohne Fenster: grab() rendert offscreen. Die stumme Pille
    zeigt bernsteinfarbene Linie und Hinweisblase; die sprechende Pine-Balken."""
    pill.cfg.pill_preview = False
    _recording(pill, level=0.0)
    pill.meter.quiet_since -= 2.0
    silent = pill.grab().toImage()
    assert _amber_pixels(silent) > 0
    _recording(pill, level=0.4)
    pill.meter.quiet_since = time.monotonic()
    talking = pill.grab().toImage()
    assert _amber_pixels(talking) == 0



# ------------------------------------------- H117 Barrierefreiheit Pille
def test_every_state_has_its_own_shape(pill):
    """Der Zustand darf nicht nur an der Farbe hängen (Farbsehschwäche)."""
    shapes = {}
    for mode in ("off", "ready", "transcribing", "done", "error"):
        pill.set_mode(mode, "x")
        shapes[mode] = tuple(pill.wave_fracs())
    assert len(set(shapes.values())) == len(shapes)


def test_pill_has_accessible_name_and_status(pill):
    from quassel import i18n
    i18n.set_language("en")
    try:
        pill.set_mode("ready")
        assert pill.accessibleName() == "Quassel"
        assert pill.accessibleDescription() == "Ready"
        pill.set_mode("error", "Server not reachable")
        assert pill.accessibleDescription() == "Error: Server not reachable"
        pill.cfg.pill_preview = False
        _recording(pill, level=0.0)
        pill.meter.quiet_since -= 2.0
        pill.tick()
        assert pill.accessibleDescription().startswith("Recording: No sound")
    finally:
        i18n.set_language(None)


def test_reduced_motion_stops_the_fallback_animation(pill, monkeypatch):
    monkeypatch.setattr(pill_qt, "reduced_motion", lambda: True)
    _recording(pill)                     # keine Pegelquelle: früher Sinus
    first = pill.wave_fracs()
    time.sleep(0.03)
    assert pill.wave_fracs() == first == list(state.LevelMeter.REST)


def test_reduced_motion_shows_a_calm_level(pill, monkeypatch):
    monkeypatch.setattr(pill_qt, "reduced_motion", lambda: True)
    _recording(pill, level=0.3)
    fr = pill.wave_fracs()
    assert fr[2] == max(fr) and fr[0] == fr[4]     # ruhiges, symmetrisches Profil


def test_reduced_motion_query_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(pill_qt, "_query_reduced_motion",
                        lambda: calls.append(1) or True)
    assert pill_qt.reduced_motion() is True
    assert pill_qt.reduced_motion() is True
    assert len(calls) == 1


def test_reduced_motion_query_failure_means_normal_motion(monkeypatch):
    def broken():
        raise OSError("kein gsettings")
    monkeypatch.setattr(pill_qt, "_query_reduced_motion", broken)
    assert pill_qt.reduced_motion() is False


def _bubble_box_rect(img, pill):
    """Bereich der Textblase: dunkle Pillenfarbe oberhalb der Pille."""
    bg = pill_qt.PILL_BG
    xs, ys = [], []
    for y in range(int(img.height() * 0.6)):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 150 and abs(c.red() - bg.red()) < 10 \
                    and abs(c.green() - bg.green()) < 10 and abs(c.blue() - bg.blue()) < 10:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def test_offscreen_bubble_sits_behind_the_text_and_stays_opaque(pill):
    """Früher lieferte boundingRect mit Qt.NoPen ein leeres Rechteck: die
    Blase lag als Eckstück links oben, der Text stand ohne Grund auf dem
    Desktop. Bei Transparenz 0,15 muss sie trotzdem deckend sein."""
    config.save({("pill", "opacity"): 0.15, ("pill", "show_preview"): "true"})
    pill.reload_cfg()
    pill.set_mode("done", "Hallo Welt")
    img = pill.grab().toImage()
    box = _bubble_box_rect(img, pill)
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 > 10 and x1 < img.width() - 10           # mittig, nicht in der Ecke
    assert y0 > 10
    alpha = img.pixelColor((x0 + x1) // 2, y0 + 2).alphaF()
    assert alpha >= pill_qt.BUBBLE_MIN_ALPHA - 0.02



# ------------------------------------------- H120 Hinweis bei Sicherer Eingabe
def test_secure_input_hint_shows_for_a_few_seconds(pill, monkeypatch):
    from quassel import i18n
    i18n.set_language("en")
    monkeypatch.setattr(pill_qt, "secure_input_enabled", lambda: True)
    try:
        pill.on = True
        pill.set_mode("ready")
        pill.tick()
        assert pill.bubble_text().startswith("Secure input is on")
        assert "Secure input" in pill.accessibleDescription()
        pill.grab()                                   # zeichnet die Blase
        updates = []
        monkeypatch.setattr(pill, "update", lambda *a: updates.append(1))
        pill.secure_until = time.monotonic() - 0.1    # Hinweiszeit vorbei
        pill.tick()
        assert pill.bubble_text() == ""
        assert updates                                # Blase verschwindet sichtbar
    finally:
        i18n.set_language(None)


def test_secure_input_hint_not_repeated_while_it_stays_on(pill, monkeypatch):
    monkeypatch.setattr(pill_qt, "secure_input_enabled", lambda: True)
    pill.on = True
    pill.set_mode("ready")
    pill.tick()
    pill.secure_until = time.monotonic() - 0.1
    pill.last_secure_poll = float("-inf")
    pill.tick()                                       # bleibt an: kein neuer Hinweis
    assert pill.bubble_text() == ""


def test_silent_mic_wins_over_secure_input_hint(pill, monkeypatch):
    monkeypatch.setattr(pill_qt, "secure_input_enabled", lambda: True)
    _recording(pill, level=0.0)
    pill.meter.quiet_since -= 2.0
    from quassel.i18n import tr
    assert pill.bubble_text() == tr("mic_silent")
