"""Win32 clipboard decisions with fake ctypes APIs, safe on macOS CI."""
import ctypes
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def paste_module(monkeypatch):
    fake = SimpleNamespace(user32=MagicMock(), kernel32=MagicMock())
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    sys.modules.pop("quassel.win.paste", None)
    module = importlib.import_module("quassel.win.paste")
    yield module
    sys.modules.pop("quassel.win.paste", None)


def test_private_formats_are_written_on_every_clipboard_copy(paste_module, monkeypatch):
    p = paste_module
    names = {name: i + 100 for i, name in enumerate((*p.PRIVATE_FORMATS, "HTML Format", "Rich Text Format"))}
    monkeypatch.setattr(p, "_format_ids", lambda: names)
    written = []
    monkeypatch.setattr(p, "_put_bytes", lambda fmt, data: written.append((fmt, data)) or True)
    p.user32.OpenClipboard.return_value = 1
    p.user32.EmptyClipboard.return_value = 1
    assert p.clip_copy("dictation")
    assert [fmt for fmt, _ in written] == [p.CF_UNICODETEXT, 100, 101, 102]
    assert all(data == b"\0" * 4 for _, data in written[1:])


def test_unknown_clipboard_format_types_without_changing_clipboard(paste_module, monkeypatch):
    p = paste_module
    monkeypatch.setattr(p, "_snapshot", lambda: None)
    typed = []
    monkeypatch.setattr(p, "_type_unicode", typed.append)
    monkeypatch.setattr(p, "clip_copy", lambda text: pytest.fail("clipboard changed"))
    p._original = None
    assert p.paste("dictation") is True
    assert typed == ["dictation"]


def test_restore_skips_new_user_copy(paste_module, monkeypatch):
    p = paste_module
    p._original = [(p.CF_UNICODETEXT, b"old\0\0")]
    p._last_written = "dictation"
    p._generation = 3
    monkeypatch.setattr(p.time, "sleep", lambda _: None)
    monkeypatch.setattr(p, "clip_read", lambda: "new user copy")
    restored = []
    monkeypatch.setattr(p, "_write_snapshot", lambda saved: restored.append(saved))
    p._restore_if_ours(0, 3)
    assert restored == []


def test_old_timer_cannot_restore_new_dictation(paste_module, monkeypatch):
    p = paste_module
    p._original = [(p.CF_UNICODETEXT, b"old\0\0")]
    p._last_written = "second"
    p._generation = 4
    monkeypatch.setattr(p.time, "sleep", lambda _: None)
    monkeypatch.setattr(p, "clip_read", lambda: "second")
    monkeypatch.setattr(p, "_write_snapshot", lambda _: pytest.fail("old timer restored"))
    p._restore_if_ours(0, 3)
