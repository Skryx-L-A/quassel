"""Live-Vorschau und Stille-Filter gibt es nur noch einmal: in dictation.py.

daemon.py (Linux/macOS) und quassel/win/app.py hatten je eine eigene Kopie
(PartialLoop/PartialWorker), deren Werte auseinanderliefen (Audit E72).
Dieser Test liest den Quelltext (ast), weil win/app.py auf macOS/Linux nur
mit Attrappen ladbar ist, und hält fest, dass keine Kopie zurückkommt."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(__file__))
DICTATION = os.path.join(ROOT, "quassel", "dictation.py")
DAEMON = os.path.join(ROOT, "quassel", "daemon.py")
WIN_APP = os.path.join(ROOT, "quassel", "win", "app.py")
CONSTANTS = ("PARTIAL_EVERY", "PARTIAL_WINDOW", "PARTIAL_MAX_WAIT",
             "LEAD_TRIM_MS", "TAIL_PAD_BT_MS")


def _tree(path):
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _assigned(tree):
    names = set()
    for node in tree.body:                    # nur Modul-Ebene
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    names.update(e.id for e in target.elts if isinstance(e, ast.Name))
    return names


def _defined(tree):
    return {node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def test_preview_constants_live_only_in_the_shared_core():
    assert set(CONSTANTS) <= _assigned(_tree(DICTATION))
    for path in (DAEMON, WIN_APP):
        own = _assigned(_tree(path)) & set(CONSTANTS)
        assert not own, "%s definiert wieder eigene Werte: %s" % (path, own)


def test_no_platform_keeps_its_own_preview_loop():
    for path in (DAEMON, WIN_APP):
        own = _defined(_tree(path)) & {"PartialLoop", "PartialWorker",
                                       "postprocess_transcript"}
        assert not own, "%s: %s" % (path, own)


def test_b37_silence_filter_is_wired_in_the_shared_core():
    helper = next(node for node in _tree(DICTATION).body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "postprocess_transcript")
    assert any(isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and isinstance(node.func.value, ast.Name)
               and node.func.value.id == "dictprompt"
               and node.func.attr == "is_silence_hallucination"
               for node in ast.walk(helper))
