"""G104 in the shared dictation core: while a program waits for an answer,
the next dictation the user starts is handed back instead of inserted.

Offline with fakes (recorder, server, paste); request files live in tmp_path."""
import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from quassel import ask, config, dictation, i18n, stats


class FileRec:
    def __init__(self, tmp_path, data=b"\x01\x00" * 20000):
        self.path = str(tmp_path / "rec.raw")
        self.data = data
        self.active = False
        self.raw_path = self.path

    def start(self, mic="default"):
        with open(self.path, "wb") as f:
            f.write(self.data)
        self.active = True
        return True

    def stop(self):
        self.active = False
        return self.path

    def raw_bytes(self, path=None):
        try:
            with open(path or self.path, "rb") as f:
                return f.read()
        except OSError:
            return b""


class NoPartial:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class Core(dictation.DictationCore):
    def __init__(self, tmp_path, rec, **cfg):
        self.rec = rec
        values = dict(
            beep=False, beep_output="system", mic="default", mute_mode="off",
            ui_language="en", streaming=True, streaming_mode="word", ai_enabled=False,
            programmer_mode=False, text_replace=False, history_enabled=True,
            stats_enabled=True, auto_learn=True, pill_enabled=True,
            reload=lambda: False)
        values.update(cfg)
        self.cfg = SimpleNamespace(**values)
        self.ducker = SimpleNamespace(apply=lambda m: None, restore=lambda: None)
        self.states, self.pasted, self.notes, self.logs = [], [], [], []
        self.streaming_started = False

    def _state(self, s, text=""):
        self.states.append((s, text))
        return None

    def _notify(self, text):
        self.notes.append(text)

    def _log(self, msg):
        self.logs.append(msg)

    def _paste(self, text):
        self.pasted.append(text)

    def _send_backspaces(self, n):
        self.pasted.append(("backspace", n))

    def _send_enter(self):
        self.pasted.append("ENTER")

    def _streaming_begin(self):
        self.streaming_started = True
        return None

    def _streaming_restore(self, backup):
        pass

    def _type_chunk(self, chunk):
        self.pasted.append(("typed", chunk))

    def _wav_from_raw(self, data, path):
        with open(path, "wb") as f:
            f.write(data)


@pytest.fixture
def env(monkeypatch, tmp_path):
    i18n.set_language("en")
    rundir = tmp_path / "run"
    rundir.mkdir()
    monkeypatch.setattr(dictation, "WAV", str(rundir / "rec.wav"))
    monkeypatch.setattr(dictation, "RUNDIR", str(rundir))
    monkeypatch.setattr(dictation, "TAIL_PAD_MS", 0)
    monkeypatch.setattr(dictation, "PartialLoop", NoPartial)
    monkeypatch.setattr(dictation.beep, "set_output", lambda n: None)
    monkeypatch.setattr(dictation.power, "install", lambda core: None)
    monkeypatch.setattr(dictation.whisperclient, "server_was_up", lambda: True)
    monkeypatch.setattr(dictation.whisperclient, "ensure_server", lambda deadline=600: True)
    transcript = {"text": "die antwort ist zwölf"}
    monkeypatch.setattr(dictation.whisperclient, "transcribe_result",
                        lambda wav, cfg: dictation.SpeechResult(transcript["text"]))
    monkeypatch.setattr(dictation.textproc, "postprocess",
                        lambda raw, cfg: ("command", "enter") if raw == "press enter"
                        else (("text", raw) if raw else (None, None)))
    monkeypatch.setattr(dictation.dictprompt, "is_silence_hallucination",
                        lambda text, pcm=None: False)
    history, learned, counted = [], [], []
    monkeypatch.setattr(config, "history_append", history.append)
    monkeypatch.setattr(config, "dictionary_learn", learned.append)
    monkeypatch.setattr(stats, "record", lambda value, seconds=0: counted.append(value))
    core = Core(tmp_path, FileRec(tmp_path))
    return SimpleNamespace(core=core, rundir=str(rundir), transcript=transcript,
                           history=history, learned=learned, counted=counted)


def _dictate(core, handsfree=False):
    assert core.start_recording()
    if handsfree:
        core.enable_streaming()
    core.finish_now()


def _answer_file(env):
    return os.path.join(env.rundir, ask.ANSWER_NAME)


def test_without_request_the_dictation_is_inserted_as_before(env):
    _dictate(env.core)
    assert env.core.pasted == ["die antwort ist zwölf"]
    assert env.history == ["die antwort ist zwölf"]
    assert not os.path.exists(_answer_file(env))


def test_pending_request_gets_the_answer_and_nothing_is_inserted(env):
    doc = ask.open_request("Wie viele?", 30, "claude", rundir=env.rundir)
    _dictate(env.core)
    assert env.core.pasted == []
    assert ask.wait_answer(doc, sleep=lambda s: None, rundir=env.rundir) == "die antwort ist zwölf"
    assert env.core.states[-1] == ("done", i18n.tr("ask_sent"))
    # The answer text is never logged and never shown in the pill.
    assert not any("zwölf" in line for line in env.core.logs)
    assert not any("zwölf" in text for _s, text in env.core.states)


def test_answer_is_no_dictation_no_history_stats_or_learning(env):
    ask.open_request("Name?", 30, rundir=env.rundir)
    _dictate(env.core)
    assert env.history == [] and env.counted == [] and env.learned == []
    assert env.core.last_paste_len == 0         # "scratch that" must not delete anything


def test_answer_is_postprocessed_text(env, monkeypatch):
    env.core.cfg.text_replace = True
    monkeypatch.setattr(config, "replacement_rules", lambda: [("zwölf", "12")])
    monkeypatch.setattr(dictation.textreplace, "apply_rules",
                        lambda text, rules: text.replace("zwölf", "12"))
    doc = ask.open_request("Wie viele?", 30, rundir=env.rundir)
    _dictate(env.core)
    assert ask.wait_answer(doc, sleep=lambda s: None, rundir=env.rundir) == "die antwort ist 12"


def test_handsfree_answer_does_not_stream_into_the_window(env):
    ask.open_request("Name?", 30, rundir=env.rundir)
    _dictate(env.core, handsfree=True)
    assert not env.core.streaming_started
    assert env.core.pasted == []


def test_request_arriving_during_a_dictation_does_not_take_its_text(env):
    assert env.core.start_recording()
    doc = ask.open_request("Name?", 30, rundir=env.rundir)
    env.core.finish_now()
    assert env.core.pasted == ["die antwort ist zwölf"]
    # The request still waits for the next dictation.
    assert ask.pending(rundir=env.rundir)["token"] == doc["token"]


def test_request_gone_before_the_end_inserts_nothing(env):
    doc = ask.open_request("Name?", 30, rundir=env.rundir)
    assert env.core.start_recording()
    ask.close_request(doc, env.rundir)          # the asker gave up meanwhile
    env.core.finish_now()
    assert env.core.pasted == []
    assert env.core.states[-1] == ("error", i18n.tr("ask_gone"))
    assert not os.path.exists(_answer_file(env))


def test_spoken_command_is_not_executed_and_the_question_keeps_waiting(env):
    env.transcript["text"] = "press enter"
    doc = ask.open_request("Name?", 30, rundir=env.rundir)
    _dictate(env.core)
    assert env.core.pasted == []
    assert env.core.states[-1] == ("error", i18n.tr("ask_no_answer"))
    assert ask.pending(rundir=env.rundir)["token"] == doc["token"]


def test_emergency_stop_without_dictation_dismisses_the_question(env):
    doc = ask.open_request("Name?", 30, rundir=env.rundir)
    env.core.panic_stop()
    assert ask.pending(rundir=env.rundir) is None
    assert env.core.notes == [i18n.tr("ask_dismissed")]
    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, sleep=lambda s: None, rundir=env.rundir)
    assert err.value.reason == "canceled"


def test_emergency_stop_during_the_answer_sends_nothing(env, monkeypatch):
    doc = ask.open_request("Name?", 30, rundir=env.rundir)
    assert env.core.start_recording()
    rest = env.core.finish()
    env.core._sync_action = "finish"            # counts as a running dictation
    env.core.panic_stop()
    env.core._sync_action = None
    rest()
    assert env.core.pasted == []
    with pytest.raises(ask.AskError) as err:
        ask.wait_answer(doc, sleep=lambda s: None, rundir=env.rundir)
    assert err.value.reason == "canceled"
    assert not os.path.exists(_answer_file(env))


def test_expired_request_is_ignored(env):
    ask.open_request("Name?", 5, now=time.time() - 60, rundir=env.rundir)
    _dictate(env.core)
    assert env.core.pasted == ["die antwort ist zwölf"]


def test_request_of_a_dead_asker_is_ignored(env):
    ask.open_request("Name?", 30, pid=999_999_999, rundir=env.rundir)
    _dictate(env.core)
    assert env.core.pasted == ["die antwort ist zwölf"]


def test_canceled_recording_leaves_the_question_waiting(env):
    doc = ask.open_request("Name?", 30, rundir=env.rundir)
    assert env.core.start_recording()
    env.core.cancel_recording("canceled_key")
    assert ask.pending(rundir=env.rundir)["token"] == doc["token"]
    _dictate(env.core)
    assert ask.wait_answer(doc, sleep=lambda s: None, rundir=env.rundir) == "die antwort ist zwölf"


def test_wake_phrase_dictation_answers_a_waiting_question(env):
    doc = ask.open_request("Name?", 30, rundir=env.rundir)
    env.core._wake_insert("die antwort ist zwölf")
    assert env.core.pasted == []
    assert ask.wait_answer(doc, sleep=lambda s: None, rundir=env.rundir) == "die antwort ist zwölf"
    assert env.history == []


def test_wake_phrase_dictation_without_question_is_inserted(env):
    env.core._wake_insert("die antwort ist zwölf")
    assert env.core.pasted == ["die antwort ist zwölf"]


def test_real_asker_thread_gets_the_answer(env):
    result = {}

    def asker():
        result["text"] = ask.ask("Name?", 10, "test", rundir=env.rundir, poll=0.01)

    thread = threading.Thread(target=asker)
    thread.start()
    deadline = time.monotonic() + 5
    while ask.pending(rundir=env.rundir) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    _dictate(env.core)
    thread.join(5)
    assert result == {"text": "die antwort ist zwölf"}
    assert os.listdir(env.rundir) == []
