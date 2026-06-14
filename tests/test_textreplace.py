"""Tests der Textersetzung / Snippet-Erweiterung (parse / apply / expand)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.textreplace import parse_rules, apply_rules, expand


def test_parse_ignores_comments_blanks_and_trims():
    rules = parse_rules(
        "\n"
        "# das ist ein Kommentar\n"
        "   # eingerueckter Kommentar\n"
        "  omw = on my way \n"        # Trigger wird getrimmt, Expansion bleibt
        "\n"
        "=keine\n"                    # leerer Trigger -> uebersprungen
        "ohnegleich\n"               # kein '=' -> uebersprungen
    )
    assert rules == [("omw", " on my way ")], rules


def test_parse_last_wins_on_duplicate():
    rules = parse_rules("omw=erste\nfoo=bar\nomw=zweite")
    # Reihenfolge bleibt (omw an Position 0), aber letzter Wert gewinnt.
    assert rules == [("omw", "zweite"), ("foo", "bar")], rules


def test_apply_whole_word_only():
    rules = [("cat", "feline")]
    assert apply_rules("category cathedral", rules) == "category cathedral"
    assert apply_rules("the cat sat", rules) == "the feline sat"


def test_apply_case_insensitive_with_capitalization_carry_over():
    rules = [("omw", "on my way")]
    assert apply_rules("Omw, see you", rules) == "On my way, see you"
    assert apply_rules("omw now", rules) == "on my way now"
    # Nur fuehrender Grossbuchstabe traegt; bestehende Grossschreibung bleibt.
    rules2 = [("usa", "United States")]
    assert apply_rules("the usa today", rules2) == "the United States today"


def test_apply_multi_word_trigger():
    rules = [("on my way", "omw")]
    assert apply_rules("I am on my way home", rules) == "I am omw home"


def test_expansion_with_regex_special_chars_is_literal():
    rules = [("myemail", "lillebor.alberti.l@gmail.com")]
    assert expand("myemail", "myemail=lillebor.alberti.l@gmail.com") == \
        "lillebor.alberti.l@gmail.com"
    # Backslashes und Gruppen-Referenzen duerfen nicht interpretiert werden.
    rules2 = [("re", r"a\1\b[x]")]
    assert apply_rules("re", rules2) == r"a\1\b[x]"


def test_longer_trigger_precedence():
    # "new york city" muss vor "new york" greifen, sonst wuerde der laengere
    # Trigger durch den kuerzeren verschattet.
    rules = [("new york", "NY"), ("new york city", "NYC")]
    assert apply_rules("I love new york city", rules) == "I love NYC"
    assert apply_rules("I love new york", rules) == "I love NY"


if __name__ == "__main__":
    for fn in [
        test_parse_ignores_comments_blanks_and_trims,
        test_parse_last_wins_on_duplicate,
        test_apply_whole_word_only,
        test_apply_case_insensitive_with_capitalization_carry_over,
        test_apply_multi_word_trigger,
        test_expansion_with_regex_special_chars_is_literal,
        test_longer_trigger_precedence,
    ]:
        fn()
    print("ok")
