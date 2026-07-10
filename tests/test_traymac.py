"""Verhaltens-Tests für traymac.py mit ECHTEM Qt (offscreen-Plattform).

Kein sys.modules-Mocking: TrayMenu wird wirklich konstruiert, set_mode
wirklich aufgerufen, Menü-Callbacks wirklich getriggert — ein kaputtes
set_mode (Review-Mutation M8) fällt hier sofort auf.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication   # noqa: E402

from quassel import traymac                   # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def tray(qapp):
    calls = {"open": 0, "quit": 0}
    t = traymac.TrayMenu(qapp,
                         on_open_center=lambda: calls.__setitem__("open", calls["open"] + 1),
                         on_quit=lambda: calls.__setitem__("quit", calls["quit"] + 1))
    t.calls = calls
    yield t
    t.hide()


def test_initial_status_is_ready(tray):
    assert tray.status_action.text() == "Bereit"
    assert not tray.status_action.isEnabled()   # Statuszeile nicht klickbar


def test_set_mode_updates_status_text(tray):
    tray.set_mode("recording")
    assert tray.status_action.text() == "Aufnahme"
    tray.set_mode("transcribing")
    assert tray.status_action.text() == "Transkription"
    tray.set_mode("error")
    assert tray.status_action.text() == "Fehler"
    tray.set_mode("off")
    assert tray.status_action.text() == "Aus"
    tray.set_mode("ready")
    assert tray.status_action.text() == "Bereit"


def test_set_mode_unknown_falls_back_to_ready(tray):
    tray.set_mode("gibberish")
    assert tray.status_action.text() == "Bereit"


def test_menu_actions_fire_callbacks(tray):
    actions = {a.text(): a for a in tray.menu.actions() if a.text()}
    actions["Kontrollzentrum öffnen"].trigger()
    assert tray.calls["open"] == 1
    actions["Beenden"].trigger()
    assert tray.calls["quit"] == 1


def test_start_tray_returns_live_traymenu(qapp):
    t = traymac.start_tray(qapp, lambda: None, lambda: None)
    try:
        assert isinstance(t, traymac.TrayMenu)
        t.set_mode("recording")
        assert t.status_action.text() == "Aufnahme"
    finally:
        t.hide()


def test_mic_icon_is_retina_sharp(qapp):
    pm = traymac._create_mic_icon(22, dpr=2.0)
    assert pm.width() == 44                    # physisch 2x
    assert pm.devicePixelRatio() == 2.0


def test_icon_is_template_mask(tray):
    # Template-Icon: macOS invertiert es passend zur Menüleiste
    assert tray.icon().isMask() is True


def test_without_toggle_callback_no_toggle_action(tray):
    assert tray.toggle_action is None
    labels = [a.text() for a in tray.menu.actions() if a.text()]
    assert "Ausschalten" not in labels and "Einschalten" not in labels


def test_toggle_action_fires_and_label_follows_mode(qapp):
    calls = {"toggle": 0}
    t = traymac.start_tray(qapp, lambda: None, lambda: None,
                           on_toggle=lambda: calls.__setitem__(
                               "toggle", calls["toggle"] + 1))
    try:
        assert t.toggle_action.text() == "Ausschalten"
        t.toggle_action.trigger()
        assert calls["toggle"] == 1
        t.set_mode("off")
        assert t.toggle_action.text() == "Einschalten"
        t.set_mode("ready")
        assert t.toggle_action.text() == "Ausschalten"
        t.set_mode("recording")             # an bleibt an
        assert t.toggle_action.text() == "Ausschalten"
    finally:
        t.hide()
