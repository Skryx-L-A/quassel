"""Erzeugt quassel/eszett_data.py aus der Wortliste enz/german-wordlist (CC0-1.0,
https://github.com/enz/german-wordlist, Datei ``words``).

Aufruf: python scripts/data/gen_eszett_data.py <pfad/zu/words>

Gespeichert wird nur, was quassel/eszett.py braucht: alle Wörter mit ß und alle
gültigen ss-Wörter („dass", „Masse", „lassen"), damit diese nie umgeschrieben
werden, auch nicht über ein zufällig passendes Wortende („l-aßen").
"""
import pathlib
import sys

words = set(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").split())
eszett = sorted(w for w in words if "ß" in w)
twins = sorted(w for w in words if "ss" in w.lower() and "ß" not in w)
out = pathlib.Path(__file__).resolve().parents[2] / "quassel" / "eszett_data.py"
out.write_text(
    '"""Generiert von scripts/data/gen_eszett_data.py aus enz/german-wordlist\n'
    '(CC0-1.0). Nicht von Hand ändern."""\n'
    "ESZETT = frozenset('''\n" + "\n".join(eszett) + "\n'''.split())\n"
    "SS_WORDS = frozenset('''\n" + "\n".join(twins) + "\n'''.split())\n",
    encoding="utf-8")
print(out, len(eszett), len(twins))
