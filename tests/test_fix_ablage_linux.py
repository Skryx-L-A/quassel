"""Offline regressions for an empty or unsafe Linux clipboard."""
from types import SimpleNamespace

from quassel import platform_linux as linux


def test_empty_clipboard_is_a_restorable_snapshot(monkeypatch):
    monkeypatch.setattr(linux, "IS_WAYLAND", True)
    monkeypatch.setattr(linux, "_run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="Nothing is copied"))
    assert linux._snapshot() == ""


def test_empty_clipboard_inserts_non_ascii_text(monkeypatch):
    monkeypatch.setattr(linux, "_snapshot", lambda: "")
    monkeypatch.setattr(linux, "_type_direct", lambda text: False)
    copied = []
    monkeypatch.setattr(linux, "clip_copy", lambda text, private=False: copied.append(text) or True)
    monkeypatch.setattr(linux, "_run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(linux.time, "sleep", lambda _: None)
    class Thread:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
    monkeypatch.setattr(linux.threading, "Thread", Thread)
    linux._original = None
    linux._prepared = None
    linux._last_written = None
    assert linux.paste("für") is True
    assert copied == ["für"]


def test_browser_text_falls_back_to_clipboard_without_touching_binary_formats(monkeypatch):
    monkeypatch.setattr(linux, "IS_WAYLAND", True)
    monkeypatch.setattr(linux, "_run", lambda args, **kwargs: SimpleNamespace(
        returncode=0, stdout="text/plain\ntext/html\ntext/_moz_htmlcontext\n"))
    assert linux._snapshot() is linux.RICH_TEXT_ONLY
    monkeypatch.setattr(linux, "_type_direct", lambda text: False)
    copied, notices = [], []
    monkeypatch.setattr(linux, "clip_copy", lambda text, private=False: copied.append(text) or True)
    monkeypatch.setattr(linux, "notify", notices.append)
    linux._original = None
    linux._prepared = None
    linux._last_written = None
    assert linux.paste("für") is True
    assert copied == ["für"]
    assert notices


def test_image_clipboard_remains_untouched(monkeypatch):
    monkeypatch.setattr(linux, "IS_WAYLAND", True)
    monkeypatch.setattr(linux, "_run", lambda args, **kwargs: SimpleNamespace(
        returncode=0, stdout="text/plain\nimage/png\n"))
    assert linux._snapshot() is None


def test_rich_text_streaming_never_restores_an_unknown_format(monkeypatch):
    monkeypatch.setattr(linux, "_snapshot", lambda: linux.RICH_TEXT_ONLY)
    monkeypatch.setattr(linux, "_type_direct", lambda text: False)
    monkeypatch.setattr(linux, "clip_copy", lambda text, private=False: True)
    monkeypatch.setattr(linux, "notify", lambda message: None)
    monkeypatch.setattr(linux, "_run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(linux.time, "sleep", lambda delay: None)
    linux._original = None
    linux._prepared = None
    linux._last_written = None
    token = linux.streaming_begin()
    assert token is linux.RICH_TEXT_ONLY
    assert linux.type_chunk("für") is True
    linux.streaming_restore(token)
    assert linux._original is None
