"""Tests des Programmierer-Diktiermodus (gesprochene Worte -> Code-Text)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.progmode import SYMBOLS, to_identifier, apply


def test_to_identifier_all_styles():
    w = ["foo", "bar"]
    assert to_identifier(w, "camel") == "fooBar"
    assert to_identifier(w, "pascal") == "FooBar"
    assert to_identifier(w, "snake") == "foo_bar"
    assert to_identifier(w, "kebab") == "foo-bar"
    assert to_identifier(w, "constant") == "FOO_BAR"
    assert to_identifier(w, "dot") == "foo.bar"
    # Eingaben werden zuerst kleingeschrieben.
    assert to_identifier(["Foo", "BAR"], "camel") == "fooBar"
    assert to_identifier([], "snake") == ""


def test_symbols_table_has_listed_keys():
    expect = {
        "open paren": "(", "klammer auf": "(",
        "close paren": ")", "klammer zu": ")",
        "open brace": "{", "geschweifte klammer auf": "{",
        "close brace": "}",
        "open bracket": "[", "eckige klammer auf": "[",
        "close bracket": "]",
        "semicolon": ";", "semikolon": ";",
        "colon": ":", "doppelpunkt": ":",
        "dot": ".", "punkt": ".",
        "comma": ",", "komma": ",",
        "equals": "=", "gleich": "=",
        "arrow": "->", "pfeil": "->",
        "underscore": "_", "unterstrich": "_",
        "dash": "-", "minus": "-", "bindestrich": "-",
        "plus": "+",
        "star": "*", "stern": "*",
        "slash": "/", "schrägstrich": "/",
        "pipe": "|", "ampersand": "&",
        "hash": "#", "raute": "#",
        "at": "@", "klammeraffe": "@",
        "percent": "%", "prozent": "%",
        "dollar": "$",
        "less than": "<", "kleiner als": "<",
        "greater than": ">", "größer als": ">",
        "bang": "!", "ausrufezeichen": "!",
        "question mark": "?", "fragezeichen": "?",
    }
    for token, sym in expect.items():
        assert SYMBOLS.get(token) == sym, (token, SYMBOLS.get(token), sym)


def test_apply_camel_span():
    assert apply("camel case foo bar end case") == "fooBar"


def test_apply_snake_span():
    assert apply("snake case my long name end case") == "my_long_name"


def test_apply_span_runs_to_sentence_end():
    # Ohne expliziten Terminator läuft die Spanne bis zum Satzende.
    assert apply("constant case max size") == "MAX_SIZE"


def test_apply_de_end_word():
    assert apply("snake case erste zeile ende") == "erste_zeile"


def test_apply_mixes_symbols():
    out = apply("foo dot bar open paren close paren")
    assert "foo.bar()" in out, out


def test_apply_de_symbol():
    # Einzelnes DE-Symbol.
    assert apply("klammer auf") == "("
    # Multiword DE-Symbol vor Einzelwort (längste Übereinstimmung gewinnt).
    assert apply("geschweifte klammer auf") == "{"


def test_apply_spacing_rules():
    # Kein Leerzeichen vor ; : . , ) ] } und keines nach ( [ {.
    assert apply("name colon value semicolon") == "name: value;"
    assert apply("a comma b") == "a, b"


def test_apply_unknown_words_passthrough():
    assert apply("hello world") == "hello world"


if __name__ == "__main__":
    tests = [
        test_to_identifier_all_styles,
        test_symbols_table_has_listed_keys,
        test_apply_camel_span,
        test_apply_snake_span,
        test_apply_span_runs_to_sentence_end,
        test_apply_de_end_word,
        test_apply_mixes_symbols,
        test_apply_de_symbol,
        test_apply_spacing_rules,
        test_apply_unknown_words_passthrough,
    ]
    for fn in tests:
        fn()
        print("ok:", fn.__name__)
    print("ok")
