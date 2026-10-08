"""Offline tests for Linux clipboard and ydotool safety guards."""
import importlib
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _linux(monkeypatch, runtime):
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


def test_paste_failure_keeps_dictation_on_the_clipboard(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    restored = []
    monkeypatch.setattr(pl, "clip_read", lambda: "user original")
    monkeypatch.setattr(pl, "clip_copy", lambda text: restored.append(text))
    monkeypatch.setattr(pl, "_run", lambda *a, **kw: type("R", (), {"returncode": 1})())
    assert pl.paste("dictation") is False
    assert restored == ["dictation"]


def test_subprocess_timeout_is_a_safe_failure(monkeypatch, tmp_path):
    pl = _linux(monkeypatch, tmp_path)
    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(pl.subprocess.TimeoutExpired(a, 3)))
    assert pl.clip_read() == ""
