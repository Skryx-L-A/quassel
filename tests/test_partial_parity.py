"""daemon.py (Linux/macOS) und quassel/win/app.py spiegeln dieselbe
Live-Vorschau-Logik (PartialLoop). win/app.py importiert ctypes.windll und ist
auf macOS/Linux nicht ladbar — dieser Test liest darum nur den Quelltext
(ast) statt zu importieren, und bleibt robust gegen Neuformatierung (kein
Zeilen-/Regex-Abgleich, sondern echte Modul-Level-Zuweisungen)."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(__file__))
DAEMON = os.path.join(ROOT, "quassel", "daemon.py")
WIN_APP = os.path.join(ROOT, "quassel", "win", "app.py")
CONSTANTS = ("PARTIAL_EVERY", "PARTIAL_WINDOW", "PARTIAL_MAX_WAIT")


def _module_level_constants(path, names):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    found = {}
    for node in tree.body:                    # nur Modul-Ebene, keine verschachtelten
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(node.value)
    return found


def test_partial_loop_constants_match_between_daemon_and_win_app():
    daemon_consts = _module_level_constants(DAEMON, CONSTANTS)
    win_consts = _module_level_constants(WIN_APP, CONSTANTS)
    assert set(daemon_consts) == set(CONSTANTS), \
        f"daemon.py fehlen Konstanten: {set(CONSTANTS) - set(daemon_consts)}"
    assert set(win_consts) == set(CONSTANTS), \
        f"win/app.py fehlen Konstanten: {set(CONSTANTS) - set(win_consts)}"
    assert daemon_consts == win_consts, \
        f"Werte weichen ab: daemon.py={daemon_consts} win/app.py={win_consts}"
