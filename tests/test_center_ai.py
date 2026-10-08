"""KI-Seite im Kontrollzentrum: „Aktualisieren" und „Test" nehmen je Endpunkt
den passenden Weg (Ollama /api/tags + /api/generate oder OpenAI-kompatibel
/v1/models + /v1/chat/completions). Offscreen, eigene config.ini, Netz nur
über ersetzte ai-Funktionen."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication              # noqa: E402

from quassel import ai, center, config, i18n           # noqa: E402


class _InlineThread:
    """Ersetzt threading.Thread: läuft sofort im Testfaden (Signale direkt)."""

    def __init__(self, target=None, daemon=None, **_kw):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def c(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    config.save({("ui", "language"): "en", ("ui", "onboarded"): "true"})
    monkeypatch.setattr(center.threading, "Thread", _InlineThread)
    win = center.Center()
    win.ai_endpoint.setText("http://127.0.0.1:8080/v1")
    yield win
    win.close()
    i18n.set_language(None)


def test_refresh_lists_models_of_an_openai_compatible_server(c, monkeypatch):
    monkeypatch.setattr(ai, "probe", lambda ep, timeout=4.0: ("openai", ["qwen3-4b"]))
    c.on_ai_refresh()
    assert [c.ai_model.itemText(i) for i in range(c.ai_model.count())][-1] == "qwen3-4b"
    assert c.ai_status.text() == ""


def test_refresh_reports_an_unreachable_address(c, monkeypatch):
    monkeypatch.setattr(ai, "probe", lambda ep, timeout=4.0: (None, []))
    c.on_ai_refresh()
    assert c.ai_status.text() == i18n.tr("ai_unavailable")


def test_test_uses_chat_completions_on_openai_servers(c, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "probe", lambda ep, timeout=4.0: ("openai", ["m"]))
    monkeypatch.setattr(ai, "chat_cleanup", lambda text, **kw: calls.append(("chat", kw)) or ("We ship Friday.", None))
    monkeypatch.setattr(ai, "generate", lambda *a, **kw: calls.append(("generate", kw)) or None)
    c.ai_model.setEditText("m")
    c.on_ai_test()
    assert [name for name, _ in calls] == ["chat"]
    assert calls[0][1]["endpoint"] == "http://127.0.0.1:8080/v1"
    assert "We ship Friday." in c.ai_test_lbl.text()


def test_test_uses_generate_on_ollama(c, monkeypatch):
    calls = []
    c.ai_endpoint.setText("http://127.0.0.1:11434")
    monkeypatch.setattr(ai, "probe", lambda ep, timeout=4.0: ("ollama", ["m"]))
    monkeypatch.setattr(ai, "chat_cleanup", lambda text, **kw: calls.append("chat") or (None, "x"))
    monkeypatch.setattr(ai, "generate", lambda *a, **kw: calls.append("generate") or "ok")
    c.on_ai_test()
    assert calls == ["generate"]
    assert c.ai_test_lbl.text().startswith("✓")
