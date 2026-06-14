"""Tests des automatischen Woerterbuch-Lernens (jargon_words/merge/learn).

Kern-Anforderung: deutsche Substantive (am Wortanfang gross) werden NICHT
gelernt -- nur klar nach Namen/Bezeichnern/Jargon aussehende Tokens."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import learn


def test_camelcase_allcaps_detected_not_sentence_start():
    # "Ich" (normale Satzanfangs-Grossschreibung) wird ausgeschlossen,
    # CamelCase und ALL-CAPS werden erkannt.
    assert learn.jargon_words("Ich nutze PyTorch und NASA auf der GPU") == \
        ["PyTorch", "NASA", "GPU"]


def test_plain_german_nouns_yield_nothing():
    # "Der" und "Hund" sind gewoehnliche Grossschreibung -> kein Jargon.
    assert learn.jargon_words("Der Hund ist schnell") == []


def test_mixed_letters_and_digits():
    assert learn.jargon_words("Wir deployen auf K8s mit GPT4") == ["K8s", "GPT4"]


def test_punctuation_is_stripped():
    assert learn.jargon_words("PyTorch,") == ["PyTorch"]
    assert learn.jargon_words("(macOS) und 'VoxType'.") == ["macOS", "VoxType"]


def test_pure_numbers_and_short_tokens_excluded():
    assert learn.jargon_words("2026 und 42 sowie x") == []


def test_distinct_first_seen_order():
    assert learn.jargon_words("GPU GPU NASA gpu") == ["GPU", "NASA"]


def test_merge_dedupes_case_insensitively_and_keeps_order():
    existing = ["PyTorch", "NASA"]
    new = ["nasa", "GPU", "gpu", "K8s"]
    # "nasa"/"gpu"-Duplikate fallen weg (case-insensitive), Reihenfolge bleibt.
    assert learn.merge_into_dictionary(new, existing) == \
        ["PyTorch", "NASA", "GPU", "K8s"]


def test_cap_keeps_most_recent():
    existing = ["A1", "B2", "C3"]
    new = ["D4", "E5"]
    assert learn.merge_into_dictionary(new, existing, cap=3) == ["C3", "D4", "E5"]


def test_learn_returns_full_dict_and_added_subset():
    existing = ["PyTorch"]
    full, added = learn.learn("Ich nutze PyTorch und NASA auf der GPU", existing)
    assert full == ["PyTorch", "NASA", "GPU"]
    assert added == ["NASA", "GPU"]   # PyTorch war schon da


if __name__ == "__main__":
    for fn in [
        test_camelcase_allcaps_detected_not_sentence_start,
        test_plain_german_nouns_yield_nothing,
        test_mixed_letters_and_digits,
        test_punctuation_is_stripped,
        test_pure_numbers_and_short_tokens_excluded,
        test_distinct_first_seen_order,
        test_merge_dedupes_case_insensitively_and_keeps_order,
        test_cap_keeps_most_recent,
        test_learn_returns_full_dict_and_added_subset,
    ]:
        fn()
    print("ok")
