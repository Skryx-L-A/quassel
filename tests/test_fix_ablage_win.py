"""Offline regressions for Windows clipboard snapshot state."""
import ctypes
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock


def _module(monkeypatch):
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
        user32=MagicMock(), kernel32=MagicMock()), raising=False)
    sys.modules.pop("quassel.win.paste", None)
    return importlib.import_module("quassel.win.paste")


def test_unsafe_clipboard_does_not_stick_after_new_copy(monkeypatch):
    win = _module(monkeypatch)
    snapshots = iter((None, [(win.CF_UNICODETEXT, b"new\0\0")]))
    monkeypatch.setattr(win, "_snapshot", lambda: next(snapshots))
    typed, copied = [], []
    monkeypatch.setattr(win, "_type_unicode", typed.append)
    monkeypatch.setattr(win, "clip_copy", lambda text: copied.append(text) or True)
    monkeypatch.setattr(win, "_send_keys", lambda steps: None)
    monkeypatch.setattr(win.time, "sleep", lambda delay: None)
    class Thread:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
    monkeypatch.setattr(win.threading, "Thread", Thread)
    assert win.paste("first") is True
    assert win.paste("second") is True
    assert typed == ["first"]
    assert copied == ["second"]


def test_empty_clipboard_snapshot_is_safe(monkeypatch):
    win = _module(monkeypatch)
    win.user32.OpenClipboard.return_value = 1
    win.user32.EnumClipboardFormats.return_value = 0
    monkeypatch.setattr(win, "_format_ids", lambda: {name: i + 100 for i, name in
                        enumerate((*win.PRIVATE_FORMATS, "HTML Format", "Rich Text Format"))})
    assert win._snapshot() == []


def test_unsafe_streaming_slot_is_rechecked_next_time(monkeypatch):
    win = _module(monkeypatch)
    snapshots = iter((None, None, [(win.CF_UNICODETEXT, b"new\0\0")]))
    monkeypatch.setattr(win, "_snapshot", lambda: next(snapshots))
    typed = []
    monkeypatch.setattr(win, "_type_unicode", typed.append)
    monkeypatch.setattr(win, "clip_copy", lambda text: True)
    monkeypatch.setattr(win, "_send_keys", lambda steps: None)
    monkeypatch.setattr(win.time, "sleep", lambda delay: None)
    assert win.streaming_begin() is None
    assert win.type_chunk("first") is True
    win.streaming_restore(None)
    assert win._original is None
    win.type_chunk("second")
    assert typed == ["first"]
