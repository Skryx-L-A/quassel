"""Offline tests for Linux clipboard and ydotool safety guards."""
import importlib
import os
import stat
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _linux(monkeypatch, runtime):
    if not hasattr(os, "getuid"):               # Windows: find_ydotool_socket fragt die UID
        monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
    sys.modules.pop("quassel.platform_linux", None)
    return importlib.import_module("quassel.platform_linux")


def test_ydotool_socket_must_be_owned_and_in_runtime_dir(tmp_path, monkeypatch):
    pl = _linux(monkeypatch, tmp_path)
    candidate = tmp_path / ".ydotool_socket"
    candidate.write_text("")
    monkeypatch.setattr(pl.os, "stat", lambda _p: type("S", (), {
        "st_uid": os.getuid() + 1, "st_mode": stat.S_IFSOCK})())
    assert pl.find_ydotool_socket() is None


def test_ydotool_socket_rejects_an_outside_environment_path(tmp_path, monkeypatch):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setenv("YDOTOOL_SOCKET", "/tmp/.ydotool_socket")
    assert pl.find_ydotool_socket() is None


def test_ydotool_socket_accepts_owned_socket_in_runtime_dir(tmp_path, monkeypatch):
    pl = _linux(monkeypatch, tmp_path)
    candidate = tmp_path / ".ydotool_socket"
    candidate.write_text("")
    monkeypatch.setattr(pl.os, "stat", lambda _p: type("S", (), {
        "st_uid": os.getuid(), "st_mode": stat.S_IFSOCK})())
    assert pl.find_ydotool_socket() == str(candidate)


def test_fast_backspaces_use_ydotool_delay_one(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(pl.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(pl, "_ydotool_supports_fast_delay", lambda: True)
    pl.send_backspaces(2)
    assert calls == [["ydotool", "key", "-d", "1", "14:1", "14:0", "14:1", "14:0"]]


def test_unknown_clipboard_format_is_not_overwritten(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setattr(pl, "_sensitive_copy_supported", lambda: True)
    restored = []
    monkeypatch.setattr(pl, "_snapshot", lambda: None)
    monkeypatch.setattr(pl, "clip_copy", lambda text: restored.append(text))
    monkeypatch.setattr(pl, "_run", lambda *a, **kw: type("R", (), {"returncode": 1})())
    monkeypatch.setattr(pl, "notify", lambda *a, **kw: None)
    assert pl.paste("dictation") is False
    assert restored == []


def test_nontext_wayland_type_is_rejected(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setattr(pl, "IS_WAYLAND", True)
    calls = []
    monkeypatch.setattr(pl, "_run", lambda args, **kw: calls.append(args) or type("R", (), {
        "returncode": 0, "stdout": "text/plain\nimage/png\n"})())
    assert pl._snapshot() is None
    assert calls == [["wl-paste", "--list-types"]]


def test_restore_preserves_new_user_copy(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    pl._original = "old"
    pl._last_written = "dictation"
    pl._generation = 2
    monkeypatch.setattr(pl.time, "sleep", lambda _: None)
    monkeypatch.setattr(pl, "clip_read", lambda: "new user copy")
    restored = []
    monkeypatch.setattr(pl, "clip_copy", restored.append)
    pl._restore_if_ours(0, 2)
    assert restored == []


def test_prepare_reads_clipboard_during_recording(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    read = threading.Event()
    monkeypatch.setattr(pl, "_snapshot", lambda: read.set() or "old")
    pl.prepare_clipboard()
    assert read.wait(1)
    with pl._clip_lock:
        assert pl._prepared == "old"


def test_direct_typing_keeps_text_out_of_argv(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "us")
    seen = []
    monkeypatch.setattr(pl, "_run", lambda args, **kw: seen.append((args, kw)) or type("R", (), {"returncode": 0})())
    assert pl._type_direct("private words")
    assert seen[0][0] == ["ydotool", "type", "--file", "-"]
    assert seen[0][1]["input"] == "private words"


def test_wayland_sensitive_copy_adds_kde_hint(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setattr(pl, "IS_WAYLAND", True)
    monkeypatch.setattr(pl, "_sensitive_copy_supported", lambda: True)
    seen = []
    monkeypatch.setattr(pl, "_run", lambda args, **kw: seen.append((args, kw)) or type("R", (), {"returncode": 0})())
    assert pl.clip_copy("dictation", private=True)
    assert seen[0][0] == ["wl-copy", "--sensitive"]


def test_without_sensitive_copy_keeps_existing_clipboard_path(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setattr(pl, "IS_WAYLAND", True)
    monkeypatch.setattr(pl, "_sensitive_copy_supported", lambda: False)
    seen = []
    monkeypatch.setattr(pl, "_run", lambda args, **kw: seen.append(args) or type("R", (), {"returncode": 0})())
    assert pl.clip_copy("dictation", private=True)
    assert seen == [["wl-copy"]]


def test_direct_typing_rejects_nonascii_instead_of_losing_letters(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setattr(pl, "_run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("ran ydotool")))
    assert pl._type_direct("für") is False


def test_subprocess_timeout_is_a_safe_failure(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(pl.subprocess.TimeoutExpired(a, 3)))
    assert pl.clip_read() == ""
