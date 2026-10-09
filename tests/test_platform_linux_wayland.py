"""Wayland display discovery uses only actual private runtime sockets."""
import importlib
import os
import socket
import sys
import tempfile
from pathlib import Path


def _load(monkeypatch, runtime):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    sys.modules.pop("quassel.platform_linux", None)
    return importlib.import_module("quassel.platform_linux")


def test_no_wayland_socket_leaves_display_unset(tmp_path, monkeypatch):
    _load(monkeypatch, tmp_path)
    assert "WAYLAND_DISPLAY" not in os.environ


def test_existing_wayland_socket_is_selected(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix=".wl-", dir=Path.cwd() / "tests") as runtime:
        path = Path(runtime) / "wayland-1"
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(path))
            pl = _load(monkeypatch, runtime)
            assert pl.IS_WAYLAND
            assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"


def test_session_env_import_passes_only_set_variables(tmp_path, monkeypatch):
    linux = _load(monkeypatch, tmp_path)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    calls = []
    names = linux.import_session_env(run=lambda args, **kw: calls.append(args))
    assert names == ["WAYLAND_DISPLAY", "XDG_SESSION_TYPE"]
    assert calls == [["systemctl", "--user", "import-environment",
                      "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"]]


def test_session_env_import_does_nothing_without_a_display(tmp_path, monkeypatch):
    linux = _load(monkeypatch, tmp_path)
    for name in linux.SESSION_VARS:
        monkeypatch.delenv(name, raising=False)
    calls = []
    assert linux.import_session_env(run=lambda args, **kw: calls.append(args)) == []
    assert calls == []


def test_quassel_ctl_imports_the_session_display_before_start(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[1]
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "calls"
    (fake / "systemctl").write_text('#!/bin/sh\necho "$*" >> "%s"\n' % log)
    (fake / "systemctl").chmod(0o755)
    env = {"PATH": "%s:/usr/bin:/bin" % fake, "WAYLAND_DISPLAY": "wayland-1"}
    subprocess.run(["bash", str(root / "bin/quassel-ctl"), "start"], env=env,
                   check=True, timeout=10)
    assert log.read_text().splitlines() == [
        "--user import-environment WAYLAND_DISPLAY",
        "--user start quasseld quassel-server quassel-pill"]
