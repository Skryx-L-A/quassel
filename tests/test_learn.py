"""Tests der Jargon-Erkennung fuers automatische Woerterbuch-Lernen
(Lebenszyklus: tests/test_learn_lifecycle.py).

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


def test_units_ordinals_and_everyday_capitals_are_not_learned():
    """"2nd", "10pm", "5EUR", "100km", "OK", "20er", "3D" gelten als Jargon,
    weil Ziffer und Buchstabe gemischt sind, sind aber nur Zahlwörter."""
    text = ("Am 2nd Mai um 10pm für 5EUR, 100km weit, OK, in den 20er Jahren, "
            "als 3D Druck, 1st 3rd 4th, 20°C, 5GB, 10:30pm, 2,5kg, 3 PM, 21ste 5ten")
    assert learn.jargon_words(text) == []


def test_real_jargon_with_digits_is_still_learned():
    assert learn.jargon_words("K8s GPT4 x86 H264 iPhone15 S3 EC2 ISO27001 3GPP") == \
        ["K8s", "GPT4", "x86", "H264", "iPhone15", "S3", "EC2", "ISO27001", "3GPP"]


if __name__ == "__main__":
    for fn in [
        test_camelcase_allcaps_detected_not_sentence_start,
        test_plain_german_nouns_yield_nothing,
        test_mixed_letters_and_digits,
        test_punctuation_is_stripped,
        test_pure_numbers_and_short_tokens_excluded,
        test_distinct_first_seen_order,
    ]:
        fn()
    print("ok")
