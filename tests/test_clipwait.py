"""Einfügen wartet auf die Zwischenablage, nicht auf eine feste Zeit (Audit C43).

Vor jedem Einfügen standen feste Pausen: macOS und Linux 250 ms, Windows
150 ms, beim Streaming 120 bzw. 80 ms. Ihr Zweck war, dass die Zwischenablage
den Text hält, bevor der Tastendruck kommt. Jetzt wird genau das geprüft; die
alte Pause bleibt die Obergrenze, falls die Ablage den Text nie zurückgibt.
"""
from types import SimpleNamespace

import pytest

from quassel import clipwait
from quassel import platform_linux as linux
from quassel import platform_mac as mac


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


def _wait(read, max_s=0.25):
    clock = Clock()
    waited = clipwait.wait_until_holds(lambda: read(clock.t), "diktat", max_s,
                                       now=clock.now, sleep=clock.sleep)
    return round(waited, 3)


def test_ready_clipboard_needs_no_wait():
    assert _wait(lambda t: "diktat") == 0.0


def test_waits_until_the_clipboard_holds_the_text():
    assert 0.04 <= _wait(lambda t: "diktat" if t >= 0.04 else "alt") <= 0.05


def test_never_waits_longer_than_the_old_pause():
    assert _wait(lambda t: "alt") == 0.25


def test_a_failing_read_waits_the_old_pause():
    def broken(t):
        raise OSError("weg")
    assert _wait(broken) == 0.25


# ------------------------------------------------------------- Plattformen

def _record_sleeps(module, monkeypatch):
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    return sleeps


def test_mac_paste_sends_at_once_when_the_pasteboard_holds_the_text(monkeypatch):
    board = {"text": "alt"}
    monkeypatch.setattr(mac, "clip_snapshot", lambda: board["text"])
    monkeypatch.setattr(mac, "clip_read", lambda: board["text"])
    monkeypatch.setattr(mac, "clip_copy", lambda text, concealed=False: board.update(text=text))
    monkeypatch.setattr(mac, "_schedule_restore_locked", lambda delay: None)
    sent = []
    monkeypatch.setattr(mac, "_send_cmd_v", lambda: sent.append(board["text"]))
    sleeps = _record_sleeps(mac, monkeypatch)
    mac._saved_original = None
    mac.paste("diktat")
    mac._saved_original = None
    mac._last_written = None
    assert sent == ["diktat"]
    assert sum(sleeps) < 0.05


def test_mac_paste_adds_a_profile_delay(monkeypatch):
    board = {"text": "alt"}
    monkeypatch.setattr(mac, "clip_snapshot", lambda: board["text"])
    monkeypatch.setattr(mac, "clip_read", lambda: board["text"])
    monkeypatch.setattr(mac, "clip_copy", lambda text, concealed=False: board.update(text=text))
    monkeypatch.setattr(mac, "_schedule_restore_locked", lambda delay: None)
    monkeypatch.setattr(mac, "_send_cmd_v", lambda: None)
    sleeps = _record_sleeps(mac, monkeypatch)
    mac._saved_original = None
    mac.paste("diktat", extra_delay=0.4)
    mac._saved_original = None
    mac._last_written = None
    assert 0.4 in sleeps


def test_linux_paste_sends_at_once_when_the_clipboard_holds_the_text(monkeypatch):
    board = {"text": "alt"}
    monkeypatch.setattr(linux, "_begin", lambda: "alt")
    monkeypatch.setattr(linux, "clip_read", lambda: board["text"])
    monkeypatch.setattr(linux, "clip_copy",
                        lambda text, private=False: board.update(text=text) or True)
    keys = []
    monkeypatch.setattr(linux, "_run", lambda args, **kw: keys.append(args) or
                        SimpleNamespace(returncode=0))

    class Thread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass
    monkeypatch.setattr(linux.threading, "Thread", Thread)
    sleeps = _record_sleeps(linux, monkeypatch)
    assert linux.paste("diktat") is True
    assert keys and keys[-1][:2] == ["ydotool", "key"]
    assert sum(sleeps) < 0.05


@pytest.fixture
def paste_module(monkeypatch):
    import ctypes
    import importlib
    import sys
    from unittest.mock import MagicMock
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=MagicMock(),
                                                          kernel32=MagicMock()), raising=False)
    sys.modules.pop("quassel.win.paste", None)
    module = importlib.import_module("quassel.win.paste")
    yield module
    sys.modules.pop("quassel.win.paste", None)


def test_windows_paste_sends_at_once_when_the_clipboard_holds_the_text(paste_module, monkeypatch):
    p = paste_module
    board = {"text": "alt"}
    monkeypatch.setattr(p, "_begin", lambda: "alt")
    monkeypatch.setattr(p, "clip_read", lambda: board["text"])
    monkeypatch.setattr(p, "clip_copy", lambda text: board.update(text=text) or True)
    sent = []
    monkeypatch.setattr(p, "_send_keys", lambda keys: sent.append(board["text"]))

    class Thread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass
    monkeypatch.setattr(p.threading, "Thread", Thread)
    sleeps = _record_sleeps(p, monkeypatch)
    assert p.paste("diktat") is True
    assert sent == ["diktat"]
    assert sum(sleeps) < 0.05


# ------------------------------------------------------------- Profil

def test_profile_reads_a_paste_delay():
    from quassel import profiles
    [p] = profiles.parse("[profile:Langsam]\nmatch = slowapp\npaste_delay_ms = 300\n")
    assert profiles.overrides_for(p)["paste_delay_ms"] == 300
    for bad in ("-5", "5000", "abc"):
        [p] = profiles.parse("[profile:X]\nmatch = x\npaste_delay_ms = %s\n" % bad)
        assert "paste_delay_ms" not in profiles.overrides_for(p)


def test_insert_passes_the_profile_delay_to_the_platform():
    from quassel import dictation, profiles
    calls = []

    class Fake:
        _paste = staticmethod(lambda text, *extra: calls.append((text, extra)))
    [p] = profiles.parse("[profile:Langsam]\nmatch = slowapp\npaste_delay_ms = 300\n")
    cfg = profiles.overlay(SimpleNamespace(), p)
    dictation.DictationCore._insert(Fake(), "a", cfg)
    dictation.DictationCore._insert(Fake(), "b")
    assert calls == [("a", (0.3,)), ("b", ())]
