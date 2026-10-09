"""i18n-Vollständigkeit (L139): jeder im Code wörtlich genannte Schlüssel
tr("…") steht in der Tabelle — sonst zeigte die Oberfläche den rohen
Schlüssel statt eines Satzes."""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import i18n

ROOT = Path(__file__).resolve().parents[1] / "quassel"


def _literal_keys():
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                yield path.relative_to(ROOT.parent), node.lineno, node.args[0].value


def test_every_literal_tr_key_exists():
    missing = [f"{p}:{line} {key}" for p, line, key in _literal_keys()
               if key not in i18n.STRINGS]
    assert missing == []


def test_scan_finds_the_keys_at_all():
    keys = {k for _p, _l, k in _literal_keys()}
    assert {"err_mic_open", "wake_listening", "model"} <= keys
