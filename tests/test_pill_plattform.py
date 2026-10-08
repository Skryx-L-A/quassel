"""Headless GNOME fallback check without importing GTK on non-Linux hosts."""
import ast
import os
from pathlib import Path


def _fallback(monkeypatch, *, layer=False, display=":0", backend=None):
    path = Path(__file__).resolve().parents[1] / "quassel" / "pill.py"
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_gnome_fallback")
    namespace = {"os": os, "HAVE_LAYER": layer}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    if display:
        monkeypatch.setenv("DISPLAY", display)
    else:
        monkeypatch.delenv("DISPLAY", raising=False)
    if backend:
        monkeypatch.setenv("GDK_BACKEND", backend)
    else:
        monkeypatch.delenv("GDK_BACKEND", raising=False)
    return namespace["_gnome_fallback"]()


def test_gnome_uses_xwayland_when_available(monkeypatch):
    assert "XWayland" in _fallback(monkeypatch)
    assert os.environ["GDK_BACKEND"] == "x11"


def test_gnome_warns_if_xwayland_unavailable(monkeypatch):
    assert "may be hidden" in _fallback(monkeypatch, display="")
    assert "GDK_BACKEND" not in os.environ


def test_layer_shell_needs_no_fallback(monkeypatch):
    assert _fallback(monkeypatch, layer=True) is None
