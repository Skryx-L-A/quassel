"""Bedienung der Textbereinigung im Kontrollzentrum: Schalter (Vorgabe aus),
Stufen, Hinweis auf den Rohtext-Rückfall, „Rohtext zurück". Offscreen, eigene
config.ini, kein echter Daemon und kein systemctl."""
import configparser
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication              # noqa: E402

from quassel import center, config, i18n               # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def c(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    config.save({("ui", "language"): "en", ("system", "onboarded"): "true"})
    win = center.Center()
    yield win
    win.close()
    i18n.set_language(None)


def saved():
    p = configparser.ConfigParser(interpolation=None)
    p.read(config.CONFIG)
    return p


def test_cleanup_is_off_by_default(c):
    assert c.cleanup_on.isChecked() is False
    assert c.cleanup_level.currentData() == "aus"
    assert not c.cleanup_level.isEnabled()


def test_levels_off_light_medium_strong(c):
    assert [c.cleanup_level.itemData(i) for i in range(c.cleanup_level.count())] == \
        ["aus", "leicht", "mittel", "stark"]
    assert c.cleanup_level.itemText(0) == "Off"
    assert c.cleanup_level.itemText(2).startswith("Medium")


def test_switching_on_saves_and_picks_light(c):
    c.cleanup_on.setChecked(True)
    p = saved()
    assert p.get("cleanup", "enabled") == "true"
    assert p.get("cleanup", "level") == "leicht"      # „an" mit „aus" täte nichts
    assert c.cleanup_level.isEnabled()
    c.cleanup_level.setCurrentIndex(c.cleanup_level.findData("stark"))
    assert saved().get("cleanup", "level") == "stark"
    c.cfg.reload(force=True)
    assert c.cfg.cleanup_enabled is True and c.cfg.cleanup_level == "stark"


def test_raw_text_fallback_is_explained(c):
    from PySide6.QtWidgets import QLabel
    texts = [w.text() for w in c.findChildren(QLabel)]
    assert any("raw text" in t and "/v1/chat/completions" in t for t in texts)


def test_raw_back_goes_to_the_controller(c):
    calls = []
    c.controller = SimpleNamespace(enabled=True, raw_back=lambda: calls.append(1) or True)
    c.raw_back_btn.click()
    assert calls == [1]
    assert c.raw_back_status.text() == i18n.tr("raw_back_sent")


def test_raw_back_on_linux_signals_the_daemon(c, monkeypatch):
    sent = []
    monkeypatch.setattr(center, "IS_WINDOWS", False)
    monkeypatch.setattr(center, "IS_MAC", False)
    monkeypatch.setattr(center, "sysctl",
                        lambda *a: sent.append(a) or SimpleNamespace(returncode=0))
    c.controller = None
    c.on_raw_back()
    assert sent == [("kill", "-s", "HUP", "quasseld.service")]


def test_raw_back_without_a_running_service(c, monkeypatch):
    c.controller = SimpleNamespace(enabled=False, raw_back=lambda: False)
    c.on_raw_back()
    assert c.raw_back_status.text() == i18n.tr("raw_back_unavailable")
