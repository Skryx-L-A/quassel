"""Audit D57: kommt der Einfüge-Tastendruck nicht an (Linux ohne laufendes
ydotoold), meldet die Pille nicht „fertig", sondern sagt, dass der Text in
der Zwischenablage liegt."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from quassel import dictation, i18n
from test_dictation_privacy import Core, setup


def run(monkeypatch, tmp_path, paste_result, **cfg):
    core = setup(monkeypatch, tmp_path)
    for k, v in cfg.items():
        setattr(core.cfg, k, v)
    core._paste = lambda text: core.pasted.append(text) or paste_result
    assert core.start_recording()
    core.finish_now()
    return core


def test_failed_keystroke_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(i18n, "current", lambda: "en")
    core = run(monkeypatch, tmp_path, False)
    state, text = core.states[-1]
    assert state == "error" and "Ctrl+V" in text
    assert core.last_paste_len == 0          # „lösch das" darf nichts löschen


def test_platforms_without_feedback_count_as_inserted(monkeypatch, tmp_path):
    core = run(monkeypatch, tmp_path, None)
    assert core.states[-1] == ("done", "hallo welt")
    assert core.last_paste_len == len("hallo welt")


def test_with_ai_the_final_text_still_reaches_the_clipboard(monkeypatch, tmp_path):
    monkeypatch.setattr(dictation.DictationCore, "_ai_refine",
                        lambda self, text: "Hallo Welt.")
    core = run(monkeypatch, tmp_path, False, ai_enabled=True)
    assert core.pasted == ["hallo welt", "Hallo Welt."]
    assert core.states[-1][0] == "error"


def test_german_hint(monkeypatch):
    monkeypatch.setattr(i18n, "current", lambda: "de")
    assert "Strg+V" in dictation.clipboard_only_text()


def test_wake_insert_reports_too(monkeypatch, tmp_path):
    core = setup(monkeypatch, tmp_path)
    core._paste = lambda text: False
    monkeypatch.setattr(dictation.textproc, "postprocess",
                        lambda raw, cfg: ("text", "hallo"))
    core._wake_insert("hallo")
    assert core.states[-1][0] == "error"
