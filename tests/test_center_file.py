"""Datei transkribieren: „Speichern unter…" für Text, SRT und WebVTT.
Offscreen; der Dateidialog wird ersetzt, geschrieben wird nur in tmp_path."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from quassel import center, i18n                       # noqa: E402
from test_center_beta import c, fake_tf, qapp          # noqa: E402,F401  (Fixtures)


def _save_dialog(monkeypatch, answer):
    asked = {}

    def fake(parent, title, suggested, filters):
        asked.update(title=title, suggested=suggested, filters=filters)
        return answer, ""
    monkeypatch.setattr(center.QFileDialog, "getSaveFileName", staticmethod(fake))
    return asked


def test_save_is_off_until_there_is_a_result(c):
    assert not c.file_save.isEnabled()


@pytest.mark.parametrize("fmt,ext,start", [("text", ".txt", "hallo welt"),
                                           ("srt", ".srt", "1\n00:00"),
                                           ("vtt", ".vtt", "WEBVTT")])
def test_save_as_writes_the_chosen_format(c, fake_tf, monkeypatch, tmp_path, fmt, ext, start):
    c.file_format.setCurrentIndex(c.file_format.findData(fmt))
    c.start_file("/tmp/talk.wav", run_async=False)
    assert c.file_save.isEnabled()
    target = tmp_path / ("out" + ext)
    asked = _save_dialog(monkeypatch, str(target))
    c.file_save.click()
    assert asked["suggested"].endswith("talk" + ext)        # Name der Quelle, passende Endung
    assert ("*" + ext) in asked["filters"]
    assert target.read_text(encoding="utf-8").startswith(start)
    assert target.read_text(encoding="utf-8").endswith("\n")
    assert c.file_status.text() == "✓ " + i18n.tr("file_saved", name="out" + ext)


def test_format_switch_after_the_run_keeps_the_extension_of_the_result(c, fake_tf, monkeypatch, tmp_path):
    c.file_format.setCurrentIndex(c.file_format.findData("srt"))
    c.start_file("/tmp/talk.wav", run_async=False)
    c.file_format.setCurrentIndex(c.file_format.findData("text"))
    asked = _save_dialog(monkeypatch, "")
    c.file_save.click()
    assert asked["suggested"].endswith("talk.srt")


def test_cancelled_dialog_writes_nothing(c, fake_tf, monkeypatch, tmp_path):
    c.start_file("/tmp/talk.wav", run_async=False)
    _save_dialog(monkeypatch, "")
    status = c.file_status.text()
    c.file_save.click()
    assert c.file_status.text() == status
    assert list(tmp_path.glob("*.txt")) == []


def test_write_error_is_reported(c, fake_tf, monkeypatch, tmp_path):
    c.start_file("/tmp/talk.wav", run_async=False)
    blocked = tmp_path / "out.txt"
    blocked.mkdir()                                   # Ziel ist ein Ordner
    _save_dialog(monkeypatch, str(blocked))
    c.file_save.click()
    assert c.file_status.text() == "✕ " + i18n.tr("file_save_failed")
