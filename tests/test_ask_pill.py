"""G104: while a program waits for an answer, both pills show the question.

Qt offscreen as in test_pill_qt.py; the GTK pill is checked without GTK by
compiling single methods as in test_pill.py. Files live in tmp_path."""
import ast
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # never a real window

from quassel import ask, config, i18n, state


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(state, "RUNDIR", str(run))
    monkeypatch.setattr(state, "STATE", str(run / "state.json"))
    i18n.set_language("en")


def test_pill_line_is_short_and_empty_without_request():
    assert ask.pill_line() == ""
    ask.open_request("Q" * 300, 30, "claude")
    line = ask.pill_line()
    assert line.startswith("Question from claude: ")
    assert line.endswith("hold the hotkey to answer")
    assert len(line) <= 140                       # fits the bubble without cutting the start


def test_pill_line_ignores_expired_request():
    ask.open_request("Name?", 5, "claude", now=time.time() - 60)
    assert ask.pill_line() == ""


# ------------------------------------------------------------------ Qt pill
@pytest.fixture
def qt_pill(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from quassel import pill_qt
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(pill_qt, "RUNDIR", state.RUNDIR)
    monkeypatch.setattr(pill_qt, "_query_reduced_motion", lambda: False)
    monkeypatch.setattr(pill_qt, "secure_input_enabled", lambda: False)
    monkeypatch.setattr(pill_qt, "daemon_active", lambda: True)
    pill = pill_qt.Pill()
    yield pill
    pill.close()


def test_qt_pill_shows_the_waiting_question_even_without_preview(qt_pill):
    qt_pill.cfg.pill_preview = False
    qt_pill.tick()
    assert qt_pill.bubble_text() == ""
    doc = ask.open_request("Which branch?", 30, "claude")
    qt_pill.tick()
    assert qt_pill.bubble_text() == ask.display(doc, ask.PILL_QUESTION_MAX)
    assert "Which branch?" in qt_pill.accessibleDescription()
    ask.discard()
    qt_pill.tick()
    assert qt_pill.bubble_text() == ""


def test_qt_pill_answer_preview_wins_over_the_question(qt_pill):
    qt_pill.cfg.pill_preview = True
    ask.open_request("Which branch?", 30, "claude")
    qt_pill.tick()
    qt_pill.set_mode("recording", "")
    assert "Which branch?" in qt_pill.bubble_text()
    qt_pill.set_mode("recording", "main bitte")
    assert qt_pill.bubble_text() == "main bitte"


def test_qt_pill_off_shows_no_question(qt_pill, monkeypatch):
    ask.open_request("Which branch?", 30, "claude")
    qt_pill.on = False
    qt_pill.last_poll = time.monotonic() + 100     # no daemon poll in this tick
    qt_pill.tick()
    assert qt_pill.ask_text == ""


# ----------------------------------------------------------------- GTK pill
def _gtk_method(name):
    path = Path(__file__).resolve().parents[1] / "quassel" / "pill.py"
    tree = ast.parse(path.read_text())
    func = next(node for cls in tree.body if isinstance(cls, ast.ClassDef)
                for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {"ask": ask}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def _gtk_pill(mode="ready"):
    shown, spoken, visible = [], [], []
    pill = SimpleNamespace(
        on=True, mode=mode, _ask_shown="",
        show_text=lambda text, italic=False, force=False: shown.append((text, force)),
        update_accessible=lambda extra="": spoken.append(extra),
        textbox=SimpleNamespace(set_visible=visible.append))
    return pill, shown, spoken, visible


def test_gtk_pill_shows_and_hides_the_waiting_question():
    sync_ask = _gtk_method("sync_ask")
    pill, shown, spoken, visible = _gtk_pill()
    sync_ask(pill)
    assert shown == [] and spoken == []
    ask.open_request("Which branch?", 30, "claude")
    sync_ask(pill)
    line = ask.pill_line()
    assert shown == [(line, True)] and spoken == [line]
    sync_ask(pill)                                 # unchanged: no redraw
    assert len(shown) == 1
    ask.discard()
    sync_ask(pill)
    assert visible == [False] and spoken[-1] == ""


def test_gtk_pill_keeps_recording_and_results_untouched():
    sync_ask = _gtk_method("sync_ask")
    ask.open_request("Which branch?", 30, "claude")
    for mode in ("recording", "transcribing", "done", "error"):
        pill, shown, _spoken, visible = _gtk_pill(mode)
        sync_ask(pill)
        assert shown == [] and visible == []
