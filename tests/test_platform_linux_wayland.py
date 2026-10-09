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
