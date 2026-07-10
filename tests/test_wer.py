import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from benchmark_stt_mac import word_error_rate


def test_identical_is_zero():
    assert word_error_rate("hello world", "hello world") == 0.0


def test_empty_reference_and_hypothesis():
    assert word_error_rate("", "") == 0.0


def test_empty_reference_nonempty_hypothesis():
    assert word_error_rate("", "hello") == 1.0


def test_full_miss():
    assert word_error_rate("hello world", "") == 1.0


def test_single_substitution():
    assert word_error_rate("hello world", "hello earth") == 0.5


def test_single_insertion():
    # 2 ref words, 1 insertion -> 1/2
    assert word_error_rate("hello world", "hello big world") == 0.5


def test_single_deletion():
    assert word_error_rate("hello big world", "hello world") == 1 / 3


def test_case_and_punctuation_ignored():
    assert word_error_rate("Hello, World!", "hello world") == 0.0


def test_german_umlauts_preserved():
    assert word_error_rate("Ich möchte Kaffee", "ich mochte kaffee") == 1 / 3
    assert word_error_rate("Ich möchte Kaffee", "ich möchte kaffee") == 0.0
