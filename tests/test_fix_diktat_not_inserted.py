"""Zusatzpunkt 8 (2026-10-09): meldet die Plattform „nicht eingefügt", sagt
die Pille nur dann „liegt in der Zwischenablage", wenn die Ablage wirklich
genau das Diktat hält. Sonst (Linux lässt Bild-/Dateiablagen absichtlich
stehen) heißt es „konnte nicht eingefügt werden", und die Aufnahme wird
gerettet, damit das Diktat nicht verloren ist."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from quassel import i18n  # noqa: E402
from test_dictation_privacy import setup  # noqa: E402


def run(monkeypatch, tmp_path, clipboard):
    monkeypatch.setattr(i18n, "current", lambda: "en")
    monkeypatch.setattr(i18n, "IS_MAC", False)
    core = setup(monkeypatch, tmp_path)
    core._paste = lambda text: core.pasted.append(text) or False
    core._clip_read = clipboard
    assert core.start_recording()
    core.finish_now()
    return core


def test_clipboard_holding_the_dictation_keeps_the_ctrl_v_hint(monkeypatch, tmp_path):
    core = run(monkeypatch, tmp_path, lambda: "hallo welt")
    state, text = core.states[-1]
    assert state == "error" and "Ctrl+V" in text
    assert not any(n.startswith("rescued-") for n in os.listdir(tmp_path))


def test_untouched_clipboard_says_not_inserted_and_rescues(monkeypatch, tmp_path):
    core = run(monkeypatch, tmp_path, lambda: "")      # Bild in der Ablage
    state, text = core.states[-1]
    assert state == "error"
    assert "Ctrl+V" not in text
    assert i18n.tr("err_not_inserted") in text
    assert any(n.startswith("rescued-") for n in os.listdir(tmp_path))
    assert core.last_paste_len == 0


def test_unreadable_clipboard_counts_as_not_inserted(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("kein Zugriff")
    core = run(monkeypatch, tmp_path, boom)
    assert i18n.tr("err_not_inserted") in core.states[-1][1]


def test_not_inserted_is_bilingual():
    en, de = i18n.STRINGS["err_not_inserted"]
    assert en.startswith("Dictation could not be inserted")
    assert de.startswith("Diktat konnte nicht eingefügt werden")
