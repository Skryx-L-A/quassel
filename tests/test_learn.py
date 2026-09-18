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


def test_cap_limits_what_is_added_and_never_deletes():
    """Die Obergrenze kürzte früher auf die LETZTEN Einträge. Die manuellen
    stehen vorne und wurden deshalb als Erste gelöscht: wer 500 Wörter hatte,
    verlor seine eigenen. Jetzt begrenzt sie nur, was neu dazukommt."""
    existing = ["A1", "B2", "C3"]
    new = ["D4", "E5"]
    assert learn.merge_into_dictionary(new, existing, cap=3) == ["A1", "B2", "C3"]
    assert learn.merge_into_dictionary(new, existing, cap=4) == ["A1", "B2", "C3", "D4"]
    assert learn.merge_into_dictionary(new, existing, cap=5) == ["A1", "B2", "C3", "D4", "E5"]


def test_a_dictionary_over_the_cap_is_left_alone():
    manual = ["Wort%d" % i for i in range(600)]
    assert learn.merge_into_dictionary(["K8s"], manual, cap=500) == manual


def test_manual_entries_survive_the_default_cap():
    manual = ["Eigenname%d" % i for i in range(500)]
    full, added = learn.learn("Ich nutze PyTorch", manual)
    assert full == manual
    assert added == []                 # nichts hinzugekommen, also nichts gemeldet


def test_learn_reports_only_what_fitted_under_the_cap():
    full, added = learn.learn("PyTorch NASA GPU", ["A1"], cap=3)
    assert full == ["A1", "PyTorch", "NASA"]
    assert added == ["PyTorch", "NASA"]


def test_units_ordinals_and_everyday_capitals_are_not_learned():
    """"2nd", "10pm", "5EUR", "100km", "OK", "20er", "3D" gelten als Jargon,
    weil Ziffer und Buchstabe gemischt sind, sind aber nur Zahlwörter."""
    text = ("Am 2nd Mai um 10pm für 5EUR, 100km weit, OK, in den 20er Jahren, "
            "als 3D Druck, 1st 3rd 4th, 20°C, 5GB, 10:30pm, 2,5kg, 3 PM, 21ste 5ten")
    assert learn.jargon_words(text) == []


def test_real_jargon_with_digits_is_still_learned():
    assert learn.jargon_words("K8s GPT4 x86 H264 iPhone15 S3 EC2 ISO27001 3GPP") == \
        ["K8s", "GPT4", "x86", "H264", "iPhone15", "S3", "EC2", "ISO27001", "3GPP"]


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
        test_cap_limits_what_is_added_and_never_deletes,
        test_learn_returns_full_dict_and_added_subset,
    ]:
        fn()
    print("ok")
