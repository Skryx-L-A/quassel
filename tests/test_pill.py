"""Check the pill launch command without importing GTK or opening a window."""
import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


def test_pill_uses_its_python_installation(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "quassel" / "pill.py"
    tree = ast.parse(path.read_text())
    method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef)
                  for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "on_left_click")
    namespace = {"subprocess": subprocess, "sys": sys}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda args: calls.append(args))
    namespace["on_left_click"](SimpleNamespace(cfg=SimpleNamespace(pill_click_opens_center=True)))
    assert calls == [[sys.executable, "-m", "quassel.center"]]
