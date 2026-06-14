"""Tests des Streaming-Tippens (word / stable / aggressive / finaler Abgleich)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.streaming import StreamTyper, split_words

class Sink:
    def __init__(self):
        self.text = ""
        self.ops = []
    def type_chunk(self, t):
        self.text += t
        self.ops.append(("type", t))
    def delete(self, n):
        self.text = self.text[:-n]
        self.ops.append(("del", n))

def test_stable_types_only_confirmed_prefix():
    s = Sink(); t = StreamTyper("stable", s.type_chunk, s.delete)
    t.update("Hallo")                       # 1. Partial: noch nichts bestätigt
    assert s.text == ""
    t.update("Hallo Welt")                  # "Hallo" jetzt 2x gesehen
    assert s.text == "Hallo", s.text
    t.update("Hallo Welt wie geht")         # "Hallo Welt" bestätigt
    assert s.text == "Hallo Welt", s.text
    assert all(op[0] == "type" for op in s.ops), "stable darf nie löschen"

def test_stable_never_deletes_on_revision():
    s = Sink(); t = StreamTyper("stable", s.type_chunk, s.delete)
    t.update("Vier Uhr"); t.update("Vier Uhr")
    assert s.text == "Vier Uhr", s.text
    t.update("Viertel Uhr")                 # Revision -> stable löscht nie
    assert s.text == "Vier Uhr", s.text     # bleibt stehen (Finale richtet es)
    assert all(op[0] == "type" for op in s.ops)

def test_finish_reconciles():
    s = Sink(); t = StreamTyper("stable", s.type_chunk, s.delete)
    t.update("Vier Uhr"); t.update("Vier Uhr")
    final = t.finish("Viertel vor acht.")
    assert s.text == "Viertel vor acht." == final

def test_aggressive_corrects_with_cap():
    s = Sink(); t = StreamTyper("aggressive", s.type_chunk, s.delete)
    t.update("Hallo Welt")
    assert s.text == "Hallo Welt"
    t.update("Hallo Werte")                 # kleine Korrektur -> Backspaces
    assert s.text == "Hallo Werte"
    assert ("del", 2) in s.ops
    # lange Passage tippen, dann ein frühes Wort revidieren -> viele
    # Backspaces nötig -> vom Cap übersprungen, bleibt bis zum Finale stehen
    long1 = "Hallo Werte " + "wort " * 40
    long2 = "Hallo XYZ " + "wort " * 40
    t.update(long1); before = s.text
    t.update(long2)                         # >120 Backspaces nötig
    assert s.text == before                 # Cap -> nicht angefasst
    t.finish(long2)                         # Finale darf unbegrenzt
    assert s.text == long2

def test_newlines_held_back():
    s = Sink(); t = StreamTyper("aggressive", s.type_chunk, s.delete)
    t.update("Erste Zeile\nzweite Zeile")
    assert "\n" not in s.text and s.text == "Erste Zeile"
    t.finish("Erste Zeile\nzweite Zeile")
    assert s.text == "Erste Zeile\nzweite Zeile"

def test_split_words_units():
    assert split_words("Hallo Welt wie") == ["Hallo", " Welt", " wie"]
    for s in (" foo  bar ", "eins", "  ", "a b c d"):
        assert "".join(split_words(s)) == s, s    # verlustfreier Round-Trip
    assert split_words("") == []

def test_word_mode_types_one_word_per_call():
    # Kern der #10-Verbesserung: mehrere neue Wörter -> EIN type-Op pro Wort,
    # nicht ein Block. (Default-Modus ist "word".)
    s = Sink(); t = StreamTyper("word", s.type_chunk, s.delete)
    t.update("Hallo Welt wie geht es")
    types = [op for op in s.ops if op[0] == "type"]
    assert len(types) == 5, types               # 5 Wörter = 5 Tipp-Ops
    assert s.text == "Hallo Welt wie geht es", s.text
    # jedes Tipp-Häppchen enthält höchstens ein Wort
    assert all(len(op[1].split()) <= 1 for op in types), types

def test_word_mode_rewrites_later():
    # Wörter werden sofort gesetzt und dürfen später revidiert werden.
    s = Sink(); t = StreamTyper("word", s.type_chunk, s.delete)
    t.update("Hallo Welt")
    assert s.text == "Hallo Welt"
    t.update("Hallo Werte")                     # kleine Revision -> Backspaces
    assert s.text == "Hallo Werte", s.text
    assert any(op[0] == "del" for op in s.ops)

if __name__ == "__main__":
    for fn in [test_stable_types_only_confirmed_prefix, test_stable_never_deletes_on_revision,
               test_finish_reconciles, test_aggressive_corrects_with_cap, test_newlines_held_back,
               test_split_words_units, test_word_mode_types_one_word_per_call,
               test_word_mode_rewrites_later]:
        fn(); print("ok:", fn.__name__)
    print("ALL STREAMING TESTS PASSED")
