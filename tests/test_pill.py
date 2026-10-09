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


def _pill_function(name, namespace):
    """Compile one function of pill.py without importing GTK."""
    path = Path(__file__).resolve().parents[1] / "quassel" / "pill.py"
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    nodes += [node for cls in tree.body if isinstance(cls, ast.ClassDef)
              for node in cls.body if isinstance(node, ast.FunctionDef)]
    func = next(node for node in nodes if node.name == name)
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def _recording_pill(preview):
    shown = []
    pill = SimpleNamespace(mode="ready", cfg=SimpleNamespace(pill_preview=preview),
                           meter=SimpleNamespace(reset=lambda: None), wave=None,
                           update_accessible=lambda text: None,
                           show_text=lambda text, italic=False, force=False:
                               shown.append((text, force)))
    return pill, shown


def test_gtk_pill_shows_listening_after_the_wake_phrase_without_preview():
    from quassel import i18n
    namespace = {"STRINGS": i18n.STRINGS, "tr": i18n.tr, "time": __import__("time")}
    set_mode = _pill_function("set_mode", namespace)
    pill, shown = _recording_pill(preview=False)
    set_mode(pill, "recording", i18n.STRINGS["wake_listening"][1])
    assert shown == [(i18n.tr("wake_listening"), True)]
    pill, shown = _recording_pill(preview=False)
    set_mode(pill, "recording", "diktierter Text")
    assert shown == [("diktierter Text", False)]   # Vorschau bleibt hinter dem Schalter


def test_gtk_pill_switches_only_its_own_profile_units(monkeypatch):
    from quassel import profile
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    calls = []
    namespace = {"profile": profile, "subprocess": SimpleNamespace(
        run=lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0))}
    namespace["daemon_unit"] = _pill_function("daemon_unit", namespace)
    namespace["daemon_active"] = lambda: True
    on_right_click = _pill_function("on_right_click", namespace)
    pill = SimpleNamespace(on=True, set_mode=lambda mode: None)
    on_right_click(pill)
    assert calls == [["systemctl", "--user", "stop",
                      "quasseld@next.service", "quassel-server@next.service"]]
