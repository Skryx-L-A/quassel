"""Tests des Streaming-Tippens mit LocalAgreement-2."""
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
    t.update("Viertel Uhr")                 # Revision -> bestätigter Text bleibt
    assert s.text == "Vier Uhr", s.text
    assert all(op[0] == "type" for op in s.ops)

def test_finish_keeps_confirmed_prefix():
    s = Sink(); t = StreamTyper("stable", s.type_chunk, s.delete)
    t.update("Vier Uhr"); t.update("Vier Uhr")
    final = t.finish("Viertel vor acht.")
    assert s.text == final == "Vier Uhr"
    assert not any(op[0] == "del" for op in s.ops)

def test_aggressive_alias_also_waits_for_confirmation():
    s = Sink(); t = StreamTyper("aggressive", s.type_chunk, s.delete)
    t.update("Hallo Welt")
    assert s.text == ""
    t.update("Hallo Werte")
    assert s.text == "Hallo"
    t.update("Hallo Werte")
    assert s.text == "Hallo Werte"
    assert not any(op[0] == "del" for op in s.ops)

def test_newlines_held_back():
    s = Sink(); t = StreamTyper("aggressive", s.type_chunk, s.delete)
    t.update("Erste Zeile\nzweite Zeile")
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
    # Mehrere bestätigte Wörter -> EIN type-Op pro Wort, kein Block.
    s = Sink(); t = StreamTyper("word", s.type_chunk, s.delete)
    t.update("Hallo Welt wie geht es")
    t.update("Hallo Welt wie geht es")
    types = [op for op in s.ops if op[0] == "type"]
    assert len(types) == 5, types               # 5 Wörter = 5 Tipp-Ops
    assert s.text == "Hallo Welt wie geht es", s.text
    # jedes Tipp-Häppchen enthält höchstens ein Wort
    assert all(len(op[1].split()) <= 1 for op in types), types

def test_word_mode_never_rewrites_later():
    s = Sink(); t = StreamTyper("word", s.type_chunk, s.delete)
    t.update("Hallo Welt"); t.update("Hallo Welt")
    assert s.text == "Hallo Welt"
    t.update("Hallo Werte")
    assert s.text == "Hallo Welt", s.text
    assert not any(op[0] == "del" for op in s.ops)

if __name__ == "__main__":
    for fn in [test_stable_types_only_confirmed_prefix, test_stable_never_deletes_on_revision,
               test_finish_keeps_confirmed_prefix, test_aggressive_alias_also_waits_for_confirmation,
               test_newlines_held_back, test_split_words_units,
               test_word_mode_types_one_word_per_call, test_word_mode_never_rewrites_later]:
        fn(); print("ok:", fn.__name__)
    print("ALL STREAMING TESTS PASSED")
