"""G104: MCP tool ask_user — off by default, never records, answers through
the next dictation the user starts. Files in tmp_path, a thread plays the
dictation core."""
import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import ask, cli, config, mcp_server, state


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(state, "RUNDIR", str(tmp_path / "run"))
    os.makedirs(state.RUNDIR)
    monkeypatch.setattr(cli, "_daemon_status", lambda: "running")
    monkeypatch.setitem(mcp_server._client, "name", "")
    notes = []
    monkeypatch.setattr(ask, "notify_user", notes.append)
    return notes


def call(arguments):
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                               "params": {"name": "ask_user", "arguments": arguments}})
    result = reply["result"]
    return result["isError"], json.loads(result["content"][0]["text"])


def enable():
    config.save({("mcp", "mcp_ask"): "true"})


def test_ask_user_is_hidden_and_refused_until_enabled():
    assert "ask_user" not in {t["name"] for t in mcp_server.tools()}
    error, body = call({"question": "Weiter?"})
    assert error and "mcp_ask" in body["error"]
    assert os.listdir(state.RUNDIR) == []
    enable()
    assert "ask_user" in {t["name"] for t in mcp_server.tools()}


def test_malformed_setting_keeps_it_off():
    with open(config.CONFIG, "w", encoding="utf-8") as sink:
        sink.write("[mcp]\nmcp_ask = maybe\n")
    assert "ask_user" not in {t["name"] for t in mcp_server.tools()}


def test_answer_comes_back_and_names_the_client(isolated):
    enable()
    mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"clientInfo": {"name": "claude-code", "version": "1"}}})
    seen = {}

    def dictation():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            doc = ask.claim()
            if doc is not None:
                seen.update(doc)
                ask.deliver(doc["token"], "Ja.")
                return
            time.sleep(0.01)

    thread = threading.Thread(target=dictation)
    thread.start()
    error, body = call({"question": "Weiter?", "timeout": 10})
    thread.join(5)
    assert not error
    assert body == {"answer": "Ja."}
    assert seen["program"] == "claude-code" and seen["question"] == "Weiter?"
    assert len(isolated) == 1
    assert os.listdir(state.RUNDIR) == []


def test_timeout_is_an_error_with_reason(monkeypatch):
    enable()
    monkeypatch.setattr(ask, "clamp_timeout", lambda value: 0.05)
    error, body = call({"question": "Weiter?", "timeout": 1})
    assert error and body["reason"] == "timeout"
    assert os.listdir(state.RUNDIR) == []


def test_second_question_is_refused():
    enable()
    ask.open_request("first", 30)
    error, body = call({"question": "second"})
    assert error and body["reason"] == "busy"


@pytest.mark.parametrize("arguments", [{}, {"question": ""}, {"question": 5},
                                       {"question": "x", "timeout": "10"},
                                       {"question": "x", "timeout": True},
                                       {"question": "x", "timeout": 0},
                                       {"question": "x", "timeout": 601}])
def test_invalid_arguments_are_refused_without_a_request(arguments):
    enable()
    error, _body = call(arguments)
    assert error
    assert os.listdir(state.RUNDIR) == []


def test_without_a_running_service_a_clear_error(monkeypatch):
    enable()
    monkeypatch.setattr(cli, "_daemon_status", lambda: "stopped")
    error, body = call({"question": "Weiter?"})
    assert error and body["reason"] == "unavailable"
    assert os.listdir(state.RUNDIR) == []


def _lines(*items):
    """stdin for serve(): JSON lines and callables, which run (and may wait)
    before the next line is read. The end of the generator is EOF."""
    for item in items:
        if callable(item):
            item()
        else:
            yield json.dumps(item) + "\n"


def _request_waits():
    deadline = time.monotonic() + 5
    while ask.pending() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ask.pending() is not None


ASK_CALL = {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "ask_user", "arguments": {"question": "Weiter?", "timeout": 3}}}


def _replied(out):
    deadline = time.monotonic() + 5
    while not out.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)


def _replies(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_cancel_notification_ends_the_question_at_once():
    # M2: the client cancels the tool call; the request must not wait on and
    # take the user's next dictation.
    import io
    enable()
    out = io.StringIO()
    gone = {}

    def request_gone():
        deadline = time.monotonic() + 5
        while ask.pending() is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        gone["after"] = time.monotonic() - started

    started = time.monotonic()
    mcp_server.serve(_lines(ASK_CALL, _request_waits,
                            {"jsonrpc": "2.0", "method": "notifications/cancelled",
                             "params": {"requestId": 5, "reason": "user"}},
                            request_gone,
                            {"jsonrpc": "2.0", "id": 6, "method": "tools/list"}), out)
    assert gone["after"] < 2
    assert os.listdir(state.RUNDIR) == []
    # No reply for the canceled call, the next request is still served.
    assert [reply["id"] for reply in _replies(out)] == [6]


def test_cancel_for_another_request_id_changes_nothing():
    import io
    enable()
    out = io.StringIO()

    def answer():
        doc = ask.claim()
        ask.deliver(doc["token"], "Ja.")

    mcp_server.serve(_lines(ASK_CALL, _request_waits,
                            {"jsonrpc": "2.0", "method": "notifications/cancelled",
                             "params": {"requestId": 99}},
                            lambda: time.sleep(0.2), answer, lambda: _replied(out)), out)
    replies = _replies(out)
    assert [reply["id"] for reply in replies] == [5]
    assert json.loads(replies[0]["result"]["content"][0]["text"]) == {"answer": "Ja."}


def test_end_of_input_ends_the_question_at_once():
    import io
    enable()
    out = io.StringIO()
    started = time.monotonic()
    mcp_server.serve(_lines(ASK_CALL, _request_waits), out)
    assert time.monotonic() - started < 2
    assert os.listdir(state.RUNDIR) == []
