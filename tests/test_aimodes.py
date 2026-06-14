"""Tests der KI-Modi-Schicht (kein laufendes Ollama, kein Internet).

Reine Helfer (BUILTIN, builtin_keys, parse_custom, all_modes, system_for,
detect_voice_mode) werden direkt geprueft. transform() wird deterministisch
getestet, indem quassel.ai.generate durch eine Fake-Funktion ersetzt wird --
es wird NIE das Netzwerk/Ollama beruehrt.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import ai, aimodes


def test_builtin_keys_and_labels():
    keys = aimodes.builtin_keys()
    for k in ("cleanup", "email", "bullets", "paragraphs", "formal", "concise"):
        assert k in keys, k
        entry = aimodes.BUILTIN[k]
        assert entry["label_en"], k
        assert entry["label_de"], k
        assert entry["system"], k


def test_parse_custom():
    assert aimodes.parse_custom("tweet=Make a tweet\n# c\n\nFOO = bar") == {
        "tweet": "Make a tweet",
        "foo": "bar",
    }
    # Spaeteres Duplikat gewinnt; leerer Name wird uebersprungen.
    assert aimodes.parse_custom("a=1\na=2\n=skip\n  \n") == {"a": "2"}
    assert aimodes.parse_custom("") == {}
    # '=' im Instruktionsteil bleibt erhalten (Split nur am ersten '=').
    assert aimodes.parse_custom("x=a=b=c") == {"x": "a=b=c"}


def test_all_modes():
    assert aimodes.all_modes("cleanup=Custom override")["cleanup"] == "Custom override"
    assert aimodes.all_modes("")["email"] == aimodes.BUILTIN["email"]["system"]
    # Built-ins sind ohne Custom-Text alle vorhanden.
    for k in aimodes.builtin_keys():
        assert aimodes.all_modes("")[k] == aimodes.BUILTIN[k]["system"]


def test_system_for():
    assert aimodes.system_for("EMAIL") == aimodes.BUILTIN["email"]["system"]
    assert aimodes.system_for("email") == aimodes.BUILTIN["email"]["system"]
    assert aimodes.system_for("nope") is None
    assert aimodes.system_for("") is None
    # Custom-Override greift auch hier.
    assert aimodes.system_for("mine", "mine=Do it") == "Do it"


def test_detect_voice_mode():
    assert aimodes.detect_voice_mode("as an email, dear team we ship friday") == (
        "email", "dear team we ship friday")
    assert aimodes.detect_voice_mode("als Liste: Milch Eier Brot")[0] == "bullets"
    assert aimodes.detect_voice_mode("als Liste: Milch Eier Brot")[1] == "Milch Eier Brot"
    assert aimodes.detect_voice_mode("just normal text") == (None, "just normal text")
    # Umlaut-Trigger und ASCII-Variante sind gleichwertig.
    assert aimodes.detect_voice_mode("foermlich der Bericht")[0] == "formal"
    assert aimodes.detect_voice_mode("förmlich der Bericht")[0] == "formal"
    assert aimodes.detect_voice_mode("förmlich der Bericht")[1] == "der Bericht"
    # Wortgrenze: "formality report" darf NICHT als "formal" matchen.
    assert aimodes.detect_voice_mode("formality report") == (None, "formality report")
    # Laengster Trigger zuerst: "as an email" schlaegt "as email"-Praefix nicht,
    # aber "turn this into an email" wird ganz konsumiert.
    assert aimodes.detect_voice_mode("turn this into an email hi there") == (
        "email", "hi there")


def test_detect_voice_mode_empty():
    assert aimodes.detect_voice_mode("") == (None, "")


class _FakeCfg:
    ai_model = "fake-model"
    ai_endpoint = "http://127.0.0.1:9"
    ai_timeout = 5.0
    ai_modes_text = ""


def test_transform_success(monkeypatch=None):
    cfg = _FakeCfg()
    orig = ai.generate
    try:
        ai.generate = lambda *a, **k: "TRANSFORMED"
        assert aimodes.transform("hi", "cleanup", cfg) == "TRANSFORMED"
    finally:
        ai.generate = orig


def test_transform_fail_soft():
    cfg = _FakeCfg()
    orig = ai.generate
    try:
        # LLM liefert nichts -> Original behalten (kein Wort verloren).
        ai.generate = lambda *a, **k: None
        assert aimodes.transform("hi", "cleanup", cfg) == "hi"
        ai.generate = lambda *a, **k: ""
        assert aimodes.transform("hi", "cleanup", cfg) == "hi"
    finally:
        ai.generate = orig


def test_transform_unknown_mode_and_empty():
    cfg = _FakeCfg()
    orig = ai.generate
    try:
        # Unbekannter Modus -> Original, ohne generate() ueberhaupt zu rufen.
        def _boom(*a, **k):
            raise AssertionError("generate must not be called for unknown mode")
        ai.generate = _boom
        assert aimodes.transform("hi", "unknownmode", cfg) == "hi"
        assert aimodes.transform("", "cleanup", cfg) == ""
    finally:
        ai.generate = orig


def test_transform_never_raises():
    cfg = _FakeCfg()
    orig = ai.generate
    try:
        # Selbst wenn generate() wirft, faellt transform weich auf das Original.
        def _raise(*a, **k):
            raise RuntimeError("boom")
        ai.generate = _raise
        assert aimodes.transform("keepme", "cleanup", cfg) == "keepme"
    finally:
        ai.generate = orig


if __name__ == "__main__":
    for fn in [test_builtin_keys_and_labels, test_parse_custom, test_all_modes,
               test_system_for, test_detect_voice_mode, test_detect_voice_mode_empty,
               test_transform_success, test_transform_fail_soft,
               test_transform_unknown_mode_and_empty, test_transform_never_raises]:
        fn()
    print("ok")
