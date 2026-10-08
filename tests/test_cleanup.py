"""Offline cleanup examples, including words that must survive."""
import pytest

from quassel.cleanup import deterministic


@pytest.mark.parametrize("raw,expected", [
    ("Ähm, ich ich komme morgen.", "ich komme morgen."),
    ("Uh, we we will meet at 3.", "we will meet at 3."),
    ("am Montag, nein, am Dienstag", "am Dienstag"),
    ("at 3, sorry, at 4", "at 4"),
    ("Also, wir beginnen jetzt.", "wir beginnen jetzt."),
    ("Das ist also richtig.", "Das ist also richtig."),
    ("Das ist halt so.", "Das ist halt so."),
    ("You know the answer.", "You know the answer."),
    ("Ich sehe das, halt, anders.", "Ich sehe das, anders."),
    ("Das dauert am Montag, nein, am Dienstag.", "Das dauert am Dienstag."),
    ("Wie spät ist es?", "Wie spät ist es?"),
    ("Wir treffen uns um drei Uhr.", "Wir treffen uns um drei Uhr."),
    ("Um, we meet at noon.", "we meet at noon."),
])
def test_deterministic(raw, expected):
    assert deterministic(raw) == expected
