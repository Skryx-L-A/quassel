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
