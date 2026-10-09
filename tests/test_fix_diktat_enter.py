"""Terminal-Schutz beim Einfügen (Review 2026-10-09, Befunde 3 und 4): die
Enter-Sperre gilt für die Anwendung, die beim Einfügen vorne ist, und
Umbrüche mitten im Text werden bei allow_enter = off zu Leerzeichen."""
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import dictation

MAIL = SimpleNamespace(cleanup_enabled=False, cleanup_level="aus",
                       ai_enabled=False, allow_enter=True, language="de")
TERMINAL = SimpleNamespace(cleanup_enabled=False, cleanup_level="aus",
                           ai_enabled=False, allow_enter=False, language="de")


def make_core(monkeypatch, raw, profile_seq):
    core = dictation.DictationCore.__new__(dictation.DictationCore)
    core.cfg = MAIL
    calls = []
    seq = list(profile_seq)
    core._profile_cfg = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
    core._wav_from_raw = lambda data, path: None
    core._refine_mechanical = lambda text: text
    core._insert = lambda text, cfg=None: calls.append(("insert", text)) or True
    core._send_enter = lambda: calls.append(("enter",))
    core._send_backspaces = lambda n: calls.append(("backspace", n))
    core._state = lambda state, text="": calls.append(("state", state)) or text
    core._forget_text = lambda text: None
    core._after_insert = lambda text, secs: None
    core._log = lambda msg: None
    core.last_paste_len = 0
    monkeypatch.setattr(dictation.whisperclient, "server_was_up", lambda: True)
    monkeypatch.setattr(dictation.whisperclient, "ensure_server", lambda deadline: True)
    monkeypatch.setattr(dictation.whisperclient, "transcribe_result",
                        lambda wav, cfg: SimpleNamespace(text=raw, language="de"))
    monkeypatch.setattr(dictation, "scratch_wav", lambda name: "unused-test.wav")
    monkeypatch.setattr(dictation, "discard", lambda path: None)
    return core, calls


def test_enter_lock_follows_the_app_in_front_at_insert_time(monkeypatch):
    """Diktat in Mail begonnen, beim Einfügen ist das Terminal vorne."""
    core, calls = make_core(monkeypatch, "ls -la\n", [MAIL, TERMINAL])
    monkeypatch.setattr(dictation, "postprocess_transcript", lambda *a: ("text", a[0]))
    core._transcribe_and_insert(None, None, b"\0" * 32000, threading.Event())
    assert ("insert", "ls -la") in calls
    assert core.last_paste_len == len("ls -la")


def test_language_still_comes_from_the_profile_before_transcription(monkeypatch):
    seen = []
    core, calls = make_core(monkeypatch, "x", [MAIL, TERMINAL])
    monkeypatch.setattr(dictation.whisperclient, "transcribe_result",
                        lambda wav, cfg: seen.append(cfg) or SimpleNamespace(text="x", language="de"))
    monkeypatch.setattr(dictation, "postprocess_transcript", lambda *a: ("text", a[0]))
    core._transcribe_and_insert(None, None, b"\0" * 32000, threading.Event())
    assert seen == [MAIL]


def test_spoken_enter_is_blocked_when_terminal_is_in_front_at_the_end(monkeypatch):
    core, calls = make_core(monkeypatch, "press enter", [MAIL, TERMINAL])
    monkeypatch.setattr(dictation, "postprocess_transcript",
                        lambda *a: ("command", "enter"))
    core._transcribe_and_insert(None, None, b"\0" * 32000, threading.Event())
    assert ("enter",) not in calls
    assert ("state", "error") in calls


def test_mail_profile_keeps_enter_and_inner_newlines(monkeypatch):
    core, calls = make_core(monkeypatch, "a\nb\n", [MAIL, MAIL])
    monkeypatch.setattr(dictation, "postprocess_transcript", lambda *a: ("text", a[0]))
    core._transcribe_and_insert(None, None, b"\0" * 32000, threading.Event())
    assert ("insert", "a\nb\n") in calls


def test_no_enter_turns_inner_newlines_into_spaces(monkeypatch):
    """Befund 4: Tipp-Rückfälle senden "\\n" als Enter."""
    core, calls = make_core(monkeypatch, "echo eins\necho zwei\n", [TERMINAL, TERMINAL])
    monkeypatch.setattr(dictation, "postprocess_transcript", lambda *a: ("text", a[0]))
    core._transcribe_and_insert(None, None, b"\0" * 32000, threading.Event())
    assert ("insert", "echo eins echo zwei") in calls


def test_finish_text_keeps_length_except_the_trailing_enter():
    out = dictation.DictationCore._finish_text("a\r\nb\nc  \n", TERMINAL)
    assert out == "a  b c"
    assert dictation.DictationCore._finish_text("a\nb\n", MAIL) == "a\nb\n"
