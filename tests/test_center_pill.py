"""Kontrollzentrum: die neue Checkbox `pill_movable` im Abschnitt sec_pill
speichert wie ihre Nachbarn (pill_show, pill_preview) — offscreen, mit einer
eigenen config.ini statt der echten (isolated_cfg)."""
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
