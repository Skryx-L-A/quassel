"""Kontrollzentrum: die Checkbox `pill_movable` im Abschnitt sec_pill
speichert wie ihre Nachbarn (pill_show, pill_preview), und der Knopf
`pill_reset_pos` daneben verwirft eine gespeicherte Ziehposition (setzt
pos_x/pos_y auf -1) — auch wenn `pill_movable` gerade aus ist — offscreen,
mit einer eigenen config.ini statt der echten (isolated_cfg)."""
import configparser
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication              # noqa: E402

from quassel import center, config                      # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))


def test_pill_movable_checkbox_saves_value(qapp, isolated_cfg):
    c = center.Center()
    try:
        assert c.pill_movable.isChecked() is False      # Vorgabe
        c.pill_movable.setChecked(True)
        saved = configparser.ConfigParser()
        saved.read(config.CONFIG)
        assert saved.getboolean("pill", "movable") is True
    finally:
        c.close()


def test_pill_click_center_checkbox_saves_value(qapp, isolated_cfg):
    c = center.Center()
    try:
        assert c.pill_click_center.isChecked() is True   # Vorgabe
        c.pill_click_center.setChecked(False)
        saved = configparser.ConfigParser()
        saved.read(config.CONFIG)
        assert saved.getboolean("pill", "click_opens_center") is False
    finally:
        c.close()


def test_reset_pos_button_writes_minus_one(qapp, isolated_cfg):
    c = center.Center()
    try:
        config.save({("pill", "pos_x"): 300, ("pill", "pos_y"): 500})
        c.pill_reset_pos.click()
        saved = configparser.ConfigParser()
        saved.read(config.CONFIG)
        assert saved.getint("pill", "pos_x") == -1
        assert saved.getint("pill", "pos_y") == -1
    finally:
        c.close()


def test_reset_pos_button_works_when_not_movable(qapp, isolated_cfg):
    c = center.Center()
    try:
        c.pill_movable.setChecked(False)
        config.save({("pill", "pos_x"): 10, ("pill", "pos_y"): 20})
        c.pill_reset_pos.click()
        saved = configparser.ConfigParser()
        saved.read(config.CONFIG)
        assert saved.getint("pill", "pos_x") == -1
        assert saved.getint("pill", "pos_y") == -1
    finally:
        c.close()


# --------------------------------------- H121 Speichern erst beim Loslassen
@pytest.fixture
def saves(monkeypatch):
    calls = []
    real = config.save

    def counting(values):
        calls.append(values)
        return real(values)
    monkeypatch.setattr(config, "save", counting)
    return calls


def test_dragging_a_slider_saves_once_on_release(qapp, isolated_cfg, saves):
    c = center.Center()
    try:
        c.pill_opacity.setSliderDown(True)
        for v in (40, 50, 60, 70):
            c.pill_opacity.setValue(v)
        assert saves == []                          # kein Schreiben während des Ziehens
        assert not c.save_timer.isActive()
        c.pill_opacity.setSliderDown(False)         # Loslassen
        assert len(saves) == 1
        saved = configparser.ConfigParser()
        saved.read(config.CONFIG)
        assert saved.getfloat("pill", "opacity") == 0.7
    finally:
        c.close()


def test_keyboard_steps_save_once_after_a_pause(qapp, isolated_cfg, saves):
    c = center.Center()
    try:
        for v in (110, 120, 130):
            c.pill_size.setValue(v)                 # Pfeiltasten: kein Ziehen
        assert saves == []
        assert c.save_timer.isActive()
        assert c.save_timer.interval() == center.SAVE_DELAY_MS
        c.save_timer.timeout.emit()
        assert len(saves) == 1
        saved = configparser.ConfigParser()
        saved.read(config.CONFIG)
        assert saved.getfloat("pill", "scale") == 1.3
    finally:
        c.close()


def test_typing_an_ai_model_does_not_save_per_keystroke(qapp, isolated_cfg, saves):
    c = center.Center()
    try:
        for text in ("l", "ll", "lla", "llama3"):
            c.ai_model.setEditText(text)
        assert saves == []
        c.save_timer.timeout.emit()
        assert len(saves) == 1
    finally:
        c.close()


def test_closing_flushes_a_pending_save(qapp, isolated_cfg, saves):
    c = center.Center()
    c.pill_size.setValue(150)
    c.close()
    assert len(saves) == 1


def test_remote_ai_endpoint_is_confirmed_once(qapp, isolated_cfg, saves, monkeypatch):
    """Nach der Bestätigung eines fremden Endpunkts fragte jedes weitere
    Speichern (jeder Regler-Schritt) erneut nach."""
    asked = []
    monkeypatch.setattr(center.QMessageBox, "question",
                        lambda *a, **k: asked.append(1) or center.QMessageBox.Yes)
    c = center.Center()
    try:
        c.ai_endpoint.setText("https://ai.example.org")
        c.save_settings()
        c.pill_size.setValue(120)
        c.save_timer.timeout.emit()
        c.pill_show.setChecked(False)
        assert len(asked) == 1
        assert len(saves) == 3
    finally:
        c.close()


# ------------------------------------------- H116 Barrierefreiheit Zentrum
def test_every_input_has_an_accessible_name(qapp, isolated_cfg):
    from PySide6.QtWidgets import (QComboBox, QLineEdit, QListWidget,
                                   QPlainTextEdit, QSlider)
    c = center.Center()
    try:
        unnamed = []
        for cls in (QComboBox, QSlider, QLineEdit, QPlainTextEdit, QListWidget):
            for w in c.findChildren(cls):
                if isinstance(w, QLineEdit) and isinstance(w.parent(), QComboBox):
                    continue                 # Editierfeld innerhalb eines Dropdowns
                if not w.accessibleName():
                    unnamed.append((cls.__name__, w.objectName()))
        assert unnamed == []
    finally:
        c.close()


def test_labeled_rows_link_label_and_field(qapp, isolated_cfg):
    from PySide6.QtWidgets import QLabel
    c = center.Center()
    try:
        buddies = {lbl.buddy() for lbl in c.findChildren(QLabel) if lbl.buddy()}
        for w in (c.pill_size, c.pill_opacity, c.mute_combo, c.lang, c.uilang):
            assert w in buddies
    finally:
        c.close()


def test_readable_reaches_aa_contrast():
    from PySide6.QtGui import QColor
    amber = QColor("#E9A93A")
    assert center.contrast_ratio(amber, "#FFFFFF") < 2.5          # Ausgangslage
    for bg in ("#FFFFFF", "#ECECEC", "#1E1E1E", "#2B2B2B"):
        assert center.contrast_ratio(center.readable(amber, bg), bg) >= 4.5
    # ausreichend kontrastreich: Farbe bleibt unverändert
    assert center.readable(QColor("#000000"), "#FFFFFF").name() == "#000000"


@pytest.mark.parametrize("window,text", [("#ECECEC", "#000000"), ("#1E1E1E", "#FFFFFF")])
def test_descriptions_and_beta_warning_meet_contrast(qapp, isolated_cfg, window, text):
    from PySide6.QtGui import QColor, QPalette
    old = qapp.palette()
    pal = QPalette(old)
    pal.setColor(QPalette.Window, QColor(window))
    pal.setColor(QPalette.WindowText, QColor(text))
    qapp.setPalette(pal)
    try:
        c = center.Center()
        try:
            assert center.contrast_ratio(c._muted, window) >= 4.5
            assert center.contrast_ratio(c._warn, window) >= 4.5
            assert f"color: {c._muted}" in c.styleSheet()
        finally:
            c.close()
    finally:
        qapp.setPalette(old)


def test_primary_button_shows_keyboard_focus(qapp, isolated_cfg):
    c = center.Center()
    try:
        assert "QPushButton:focus" in c.toggle_btn.styleSheet()
    finally:
        c.close()
