"""Knopf „Diagnose kopieren“ im Kontrollzentrum (offscreen, eigene config.ini,
Zwischenablage und Speicherdialog als Attrappen: die echte Zwischenablage
bleibt unberührt)."""
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication              # noqa: E402

from quassel import center, config, diagnostics, i18n, whisperclient  # noqa: E402

DICTATED = "Das hier habe ich diktiert und es darf nie raus"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeClipboard:
    def __init__(self):
        self.text = None

    def setText(self, text):
        self.text = text


@pytest.fixture
def c(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "SERVERENV", str(tmp_path / "server.env"))
    monkeypatch.setattr(config, "HISTORY", str(tmp_path / "history.jsonl"))
    (tmp_path / "history.jsonl").write_text('{"text": "%s"}\n' % DICTATED, encoding="utf-8")
    (tmp_path / "server.env").write_text("REQUEST_PATH=/q-secretpath\n", encoding="utf-8")
    log = tmp_path / "daemon.log"
    log.write_text("10:00 dict: rec=1.0s\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "daemon_log_file", lambda: str(log))
    monkeypatch.setattr(whisperclient, "server_up", lambda timeout=2: False)
    clip = FakeClipboard()
    monkeypatch.setattr(center.QApplication, "clipboard", staticmethod(lambda: clip))
    # onboarded: sonst öffnete processEvents() den modalen Willkommensdialog
    config.save({("ui", "language"): "en", ("system", "onboarded"): "true"})
    win = center.Center()
    win.clip = clip
    yield win
    win.close()
    i18n.set_language(None)


def test_button_is_on_the_about_page_with_accessible_name(c):
    page = c.stack.widget(c._page_keys.index("about"))
    assert page.isAncestorOf(c.diag_copy)
    assert c.diag_copy.text() == "Copy diagnostics"
    assert c.diag_copy.accessibleName() == "Copy diagnostics"
    assert c.diag_save.accessibleName() == "Save as file…"
    assert "never contains" in c.diag_hint.text()


def test_copy_puts_report_on_the_qt_clipboard(c):
    c.on_diag("copy", run_async=False)
    report = c.clip.text
    assert report.startswith("Quassel diagnostics")
    assert "reachable: no" in report
    assert "q-secretpath" not in report and "REQUEST_PATH=<entfernt>" in report
    assert DICTATED not in report
    assert c.diag_status.text().startswith("Copied to the clipboard (")
    assert c.diag_copy.isEnabled() and c.diag_save.isEnabled()


def test_copy_does_not_use_the_dictation_paste_path(c, monkeypatch):
    def forbidden(*_a, **_kw):
        raise AssertionError("dictation clipboard path used")
    monkeypatch.setattr(center, "clip_copy", forbidden)
    c.on_diag("copy", run_async=False)
    assert c.clip.text


def test_german_labels(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    config.save({("ui", "language"): "de", ("system", "onboarded"): "true"})
    win = center.Center()
    try:
        assert win.diag_copy.text() == "Diagnose kopieren"
        assert win.diag_copy.accessibleName() == "Diagnose kopieren"
    finally:
        win.close()
        i18n.set_language(None)


@pytest.mark.skipif(os.name == "nt", reason="POSIX-Dateirechte")
def test_save_writes_private_file(c, tmp_path, monkeypatch):
    target = tmp_path / "out" / "diag.txt"
    monkeypatch.setattr(center.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (str(target), "")))
    c.on_diag("save", run_async=False)
    assert target.read_text(encoding="utf-8").startswith("Quassel diagnostics")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert c.diag_status.text() == "Saved as diag.txt."
    assert c.clip.text is None          # Speichern kopiert nicht nebenbei


def test_save_cancelled(c, monkeypatch):
    monkeypatch.setattr(center.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **kw: ("", "")))
    c.on_diag("save", run_async=False)
    assert c.diag_status.text() == ""


def test_save_failure_is_reported(c, tmp_path, monkeypatch):
    monkeypatch.setattr(center.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (str(tmp_path / "x.txt"), "")))

    def fail(path, text):
        raise OSError("read-only")
    monkeypatch.setattr(diagnostics, "save_report", fail)
    c.on_diag("save", run_async=False)
    assert c.diag_status.text() == "Could not save the file."


def test_async_collection_delivers_on_the_gui_thread(c, qapp):
    import time
    c.on_diag("copy")
    assert not c.diag_copy.isEnabled()
    deadline = time.monotonic() + 5
    while c.clip.text is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert c.clip.text and c.clip.text.startswith("Quassel diagnostics")
    assert c.diag_copy.isEnabled()
