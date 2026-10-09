"""G103: `quassel-type-cli dictate --once` waits for the user's next dictation.

The command never records: a fake "dictation" thread answers through the
files in tmp_path, as the dictation core would."""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import ask, cli, config, state


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(state, "RUNDIR", str(tmp_path / "run"))
    os.makedirs(state.RUNDIR)
    monkeypatch.setattr(cli, "_daemon_status", lambda: "running")
    notes = []
    monkeypatch.setattr(ask, "notify_user", notes.append)
    return notes


def _answer_when_asked(action):
    def run():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            doc = ask.pending()
            if doc is not None:
                action(doc)
                return
            time.sleep(0.01)
    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_prints_the_answer_and_exits_zero(isolated, capsys):
    seen = {}

    def answer(doc):
        seen.update(doc)
        assert ask.claim()["token"] == doc["token"]
        assert ask.deliver(doc["token"], "Ja, bitte.")

    thread = _answer_when_asked(answer)
    code = cli.main(["dictate", "--once", "--prompt", "Weiter?", "--timeout", "10",
                     "--from", "claude"])
    thread.join(5)
    out = capsys.readouterr()
    assert code == 0
    assert out.out == "Ja, bitte.\n"
    assert seen["question"] == "Weiter?" and seen["program"] == "claude"
    assert seen["expires"] - seen["created"] == 10
    assert [doc["question"] for doc in isolated] == ["Weiter?"]   # notified once
    assert os.listdir(state.RUNDIR) == []


def test_timeout_has_its_own_exit_code_and_leaves_nothing(monkeypatch, capsys):
    monkeypatch.setattr(ask, "clamp_timeout", lambda value: 0.05)
    code = cli.main(["dictate", "--once", "--timeout", "1"])
    assert code == cli.EXIT_TIMEOUT
    assert capsys.readouterr().out == ""
    assert os.listdir(state.RUNDIR) == []


def test_second_question_is_refused(capsys):
    first = ask.open_request("first", 30)
    code = cli.main(["dictate", "--once", "--timeout", "5"])
    assert code == cli.EXIT_BUSY
    assert "already waiting" in capsys.readouterr().err
    assert ask.pending()["token"] == first["token"]


def test_emergency_stop_ends_the_wait(capsys):
    thread = _answer_when_asked(lambda doc: ask.discard())
    code = cli.main(["dictate", "--once", "--timeout", "10"])
    thread.join(5)
    assert code == cli.EXIT_CANCELED
    assert capsys.readouterr().out == ""
    assert os.listdir(state.RUNDIR) == []


def test_without_a_running_service_a_clear_error_and_no_request(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_daemon_status", lambda: "stopped")
    code = cli.main(["dictate", "--once"])
    assert code == cli.EXIT_UNAVAILABLE
    assert "not running" in capsys.readouterr().err
    assert os.listdir(state.RUNDIR) == []


def test_interrupt_removes_the_request(monkeypatch, capsys):
    def interrupted(doc, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(ask, "wait_answer", interrupted)
    assert cli.main(["dictate", "--once"]) == cli.EXIT_CANCELED
    assert os.listdir(state.RUNDIR) == []


@pytest.mark.parametrize("args", [["dictate"], ["dictate", "--once", "--timeout", "0"],
                                  ["dictate", "--once", "--timeout", "601"],
                                  ["dictate", "--once", "--timeout", "soon"]])
def test_invalid_arguments_are_usage_errors(args, capsys):
    with pytest.raises(SystemExit) as exit_:
        cli.main(args)
    assert exit_.value.code == cli.EXIT_USAGE
    assert os.listdir(state.RUNDIR) == []


def test_display_names_asker_question_and_how_to_answer(monkeypatch):
    from quassel import i18n
    i18n.set_language("de")
    try:
        assert ask.display({"question": "Weiter?", "program": "claude"}) == \
            "Frage von claude: Weiter? — Hotkey halten zum Antworten"
        assert ask.display({"question": "", "program": ""}) == \
            "Ein Programm wartet auf eine Antwort — Hotkey halten zum Antworten"
    finally:
        i18n.set_language("en")
    assert ask.display({"question": "Go on?", "program": ""}) == \
        "Question: Go on? — hold the hotkey to answer"
