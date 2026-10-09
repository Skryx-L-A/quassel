"""G104: request and answer files of „ask the user by voice" (quassel/ask.py).

Everything lives in tmp_path; no microphone, no daemon, no real run directory."""
import json
import os
import stat
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import ask


def _path(tmp_path, name):
    return str(tmp_path / name)


def test_request_is_private_and_carries_token_question_deadline_pid(tmp_path):
    doc = ask.open_request("Which branch?", 30, "claude", now=1000.0, rundir=str(tmp_path))
    path = _path(tmp_path, ask.REQUEST_NAME)
    with open(path, encoding="utf-8") as source:
        on_disk = json.load(source)
    assert on_disk == doc
    assert len(doc["token"]) == 32
    assert doc["question"] == "Which branch?"
    assert doc["program"] == "claude"
    assert doc["pid"] == os.getpid()
    assert doc["expires"] == 1030.0
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # No staging file stays behind.
    assert sorted(os.listdir(tmp_path)) == [ask.REQUEST_NAME]


def test_question_is_shortened_to_one_line(tmp_path):
    doc = ask.open_request("line one\nline\ttwo\x1b[31m " + "x" * 400, 30,
                           rundir=str(tmp_path))
    assert "\n" not in doc["question"] and "\x1b" not in doc["question"]
    assert len(doc["question"]) == ask.QUESTION_MAX
    assert doc["question"].endswith("…")


def test_timeout_defaults_and_caps():
    assert ask.clamp_timeout(None) == ask.DEFAULT_TIMEOUT
    assert ask.clamp_timeout("nonsense") == ask.DEFAULT_TIMEOUT
    assert ask.clamp_timeout(-5) == ask.DEFAULT_TIMEOUT
    assert ask.clamp_timeout(float("nan")) == ask.DEFAULT_TIMEOUT
    assert ask.clamp_timeout(10_000) == ask.MAX_TIMEOUT
    assert ask.clamp_timeout(45) == 45


def test_answer_reaches_the_asker_and_files_are_gone(tmp_path):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    claimed = ask.claim(rundir=rundir)
    assert claimed["token"] == doc["token"]
    assert ask.deliver(doc["token"], "Henrike", rundir=rundir)
    assert not os.path.exists(_path(tmp_path, ask.REQUEST_NAME))
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(_path(tmp_path, ask.ANSWER_NAME)).st_mode) == 0o600
    assert ask.wait_answer(doc, sleep=lambda s: None, rundir=rundir) == "Henrike"
    assert os.listdir(tmp_path) == []


def test_timeout_ends_empty_and_removes_the_request(tmp_path):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 5, now=time.time() - 10, rundir=rundir)
    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, sleep=lambda s: None, rundir=rundir)
    assert err.value.reason == "timeout"
    assert os.listdir(tmp_path) == []


def test_waiting_until_the_deadline_then_timeout(tmp_path):
    rundir = str(tmp_path)
    clock = [1000.0]
    doc = ask.open_request("Name?", 2, now=clock[0], rundir=rundir)
    # pending() checks the deadline against the real clock: keep it in the future.
    doc["expires"] = clock[0] + 2

    def sleep(seconds):
        clock[0] += seconds

    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, poll=0.5, sleep=sleep, clock=lambda: clock[0], rundir=rundir)
    assert err.value.reason == "timeout"
    assert clock[0] == 1002.0


def test_a_claimed_request_waits_past_the_deadline_for_the_answer(tmp_path):
    rundir = str(tmp_path)
    now = time.time()
    doc = ask.open_request("Name?", 5, now=now - 10, rundir=rundir)
    assert ask.pending(rundir=rundir) is None          # past its deadline
    with open(_path(tmp_path, ask.CLAIM_NAME), "w", encoding="utf-8") as sink:
        json.dump({"token": doc["token"]}, sink)
    assert ask.pending(rundir=rundir)["token"] == doc["token"]
    assert ask.deliver(doc["token"], "late but meant", rundir=rundir)
    assert ask.wait_answer(doc, sleep=lambda s: None, rundir=rundir) == "late but meant"


def test_second_request_is_refused_while_one_waits(tmp_path):
    rundir = str(tmp_path)
    first = ask.open_request("first", 30, rundir=rundir)
    with pytest.raises(ask.AskError) as err:
        ask.open_request("second", 30, rundir=rundir)
    assert err.value.reason == "busy"
    assert ask.pending(rundir=rundir)["token"] == first["token"]
    assert sorted(os.listdir(tmp_path)) == [ask.REQUEST_NAME]


def test_stale_request_of_a_dead_asker_is_replaced(tmp_path, monkeypatch):
    rundir = str(tmp_path)
    ask.open_request("old", 30, pid=999_999_999, rundir=rundir)
    assert ask.pending(rundir=rundir) is None          # its asker is gone
    new = ask.open_request("new", 30, rundir=rundir)
    assert ask.pending(rundir=rundir)["token"] == new["token"]


def test_expired_request_is_replaced(tmp_path):
    rundir = str(tmp_path)
    ask.open_request("old", 5, now=time.time() - 60, rundir=rundir)
    new = ask.open_request("new", 30, rundir=rundir)
    assert ask.pending(rundir=rundir)["token"] == new["token"]


def test_emergency_stop_dismisses_and_the_asker_learns_it(tmp_path):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    assert ask.discard(rundir=rundir) is True
    assert ask.pending(rundir=rundir) is None
    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, sleep=lambda s: None, rundir=rundir)
    assert err.value.reason == "canceled"
    assert os.listdir(tmp_path) == []
    assert ask.discard(rundir=rundir) is False          # nothing waits any more


def test_wrong_token_is_neither_delivered_nor_read(tmp_path):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    assert not ask.deliver("0" * 32, "for someone else", rundir=rundir)
    assert not os.path.exists(_path(tmp_path, ask.ANSWER_NAME))
    # A foreign answer file is ignored by the asker.
    with open(_path(tmp_path, ask.ANSWER_NAME), "w", encoding="utf-8") as sink:
        json.dump({"token": "f" * 32, "text": "foreign"}, sink)
    doc["expires"] = time.time() - 1
    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, sleep=lambda s: None, rundir=rundir)
    assert err.value.reason == "timeout"
    assert os.path.exists(_path(tmp_path, ask.ANSWER_NAME))   # not ours: untouched


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(token="not-hex"),
    lambda d: d.update(profile="other"),
    lambda d: d.update(expires=d["created"] + ask.MAX_TIMEOUT + 60),
    lambda d: d.update(pid="1"),
    lambda d: d.update(question=None),
])
def test_malformed_or_foreign_requests_are_ignored(tmp_path, mutate):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    mutate(doc)
    with open(_path(tmp_path, ask.REQUEST_NAME), "w", encoding="utf-8") as sink:
        json.dump(doc, sink)
    assert ask.pending(rundir=rundir) is None
    assert ask.claim(rundir=rundir) is None


def test_replaced_request_ends_the_first_asker(tmp_path):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    other = dict(doc, token="a" * 32)
    with open(_path(tmp_path, ask.REQUEST_NAME), "w", encoding="utf-8") as sink:
        json.dump(other, sink)
    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, sleep=lambda s: None, rundir=rundir)
    assert err.value.reason == "busy"


def test_orphaned_answer_is_swept(tmp_path):
    rundir = str(tmp_path)
    answer = _path(tmp_path, ask.ANSWER_NAME)
    with open(answer, "w", encoding="utf-8") as sink:
        json.dump({"token": "b" * 32, "text": "never read"}, sink)
    old = time.time() - ask.ORPHAN_AGE - 5
    os.utime(answer, (old, old))
    ask.open_request("Name?", 30, rundir=rundir)
    assert not os.path.exists(answer)


def test_ask_notifies_and_cleans_up_on_interrupt(tmp_path):
    rundir = str(tmp_path)
    seen = []

    def interrupt(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ask.ask("Name?", 30, "agent", notify=seen.append, rundir=rundir, sleep=interrupt)
    assert seen and seen[0]["question"] == "Name?"
    assert os.listdir(tmp_path) == []


def test_pid_alive_rejects_nonsense():
    assert ask.pid_alive(os.getpid())
    assert not ask.pid_alive(0)
    assert not ask.pid_alive(True)
    assert not ask.pid_alive("12")
    assert not ask.pid_alive(999_999_999)


def test_second_claim_on_the_same_request_is_refused(tmp_path):
    # M1: dictation A answers, dictation B starts while A transcribes. B must
    # not bind to the same request, else its text is lost when A delivers.
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    assert ask.claim(rundir=rundir)["token"] == doc["token"]
    assert ask.claim(rundir=rundir) is None
    assert ask.deliver(doc["token"], "A", rundir=rundir)
    assert ask.wait_answer(doc, sleep=lambda s: None, rundir=rundir) == "A"


def test_stale_claim_of_another_request_does_not_block(tmp_path):
    rundir = str(tmp_path)
    with open(_path(tmp_path, ask.CLAIM_NAME), "w", encoding="utf-8") as sink:
        json.dump({"token": "f" * 32}, sink)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    assert ask.claim(rundir=rundir)["token"] == doc["token"]
    assert ask.claim(rundir=rundir) is None


def test_released_claim_can_be_claimed_again(tmp_path):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    assert ask.claim(rundir=rundir) is not None
    ask.release(doc["token"], rundir=rundir)
    assert ask.claim(rundir=rundir)["token"] == doc["token"]
    assert sorted(os.listdir(tmp_path)) == [ask.CLAIM_NAME, ask.REQUEST_NAME]


def test_late_delivery_does_not_remove_a_newer_request(tmp_path, monkeypatch):
    # N3: deliver for A passed pending() just before A ended and B asked.
    rundir = str(tmp_path)
    doc_a = ask.open_request("A?", 30, rundir=rundir)
    ask.close_request(doc_a, rundir)
    doc_b = ask.open_request("B?", 30, rundir=rundir)
    monkeypatch.setattr(ask, "pending", lambda now=None, rundir=None: doc_a)
    ask.deliver(doc_a["token"], "for A", rundir=rundir)
    monkeypatch.undo()
    assert ask.pending(rundir=rundir)["token"] == doc_b["token"]


def test_racing_askers_do_not_remove_each_others_request(tmp_path, monkeypatch):
    # N3: two askers see the same expired request; the faster one links its
    # own. The slower one must get busy, not delete the fresh request.
    rundir = str(tmp_path)
    ask.open_request("old", 5, now=time.time() - 60, rundir=rundir)
    real_pending = ask.pending
    winner = {}

    def pending_then_lose_the_race(now=None, rundir=None):
        if not winner:
            os.remove(os.path.join(rundir, ask.REQUEST_NAME))
            monkeypatch.setattr(ask, "pending", real_pending)
            winner["doc"] = ask.open_request("fast", 30, rundir=rundir)
            monkeypatch.setattr(ask, "pending", pending_then_lose_the_race)
            return None                 # what the slow asker saw before
        return real_pending(now, rundir)

    monkeypatch.setattr(ask, "pending", pending_then_lose_the_race)
    with pytest.raises(ask.AskError) as err:
        ask.open_request("slow", 30, rundir=rundir)
    assert err.value.reason == "busy"
    monkeypatch.undo()
    assert ask.pending(rundir=rundir)["token"] == winner["doc"]["token"]


def test_stop_ends_the_wait_as_canceled_and_cleans_up(tmp_path):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    calls = []
    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, sleep=lambda s: calls.append(s), rundir=rundir,
                        stop=lambda: len(calls) >= 2)
    assert err.value.reason == "canceled"
    assert os.listdir(tmp_path) == []


def test_failed_answer_reaches_the_asker_as_failed(tmp_path):
    rundir = str(tmp_path)
    doc = ask.open_request("Name?", 30, rundir=rundir)
    ask.claim(rundir=rundir)
    assert ask.fail(doc["token"], rundir=rundir)
    assert ask.pending(rundir=rundir) is None
    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, sleep=lambda s: None, rundir=rundir)
    assert err.value.reason == "failed"
    assert os.listdir(tmp_path) == []
    assert not ask.fail(doc["token"], rundir=rundir)


@pytest.mark.parametrize("raw, shown", [
    ("Quassel‮gnp.exe", "Quasselgnp.exe"),        # right-to-left override
    ("⁦claude⁩ code", "claude code"),          # isolates
    ("cl​au‍de﻿", "claude"),              # zero width
    ("a‎b‏c؜d­e", "abcde"),          # marks, soft hyphen
    ("one two", "one two"),
])
def test_bidi_and_invisible_characters_are_removed(raw, shown):
    # N5: a client name or question must not disguise who asks or what.
    assert ask.shorten(raw, 80) == shown


def test_request_keeps_no_bidi_controls(tmp_path):
    doc = ask.open_request("Delete ‮all?", 30, "Quassel‮", rundir=str(tmp_path))
    assert "‮" not in doc["question"] and "‮" not in doc["program"]
