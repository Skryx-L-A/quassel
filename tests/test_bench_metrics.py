"""Tests für die Metriken des Messrahmens (WER, sichtbare Fehler, Perzentile)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bench import metrics  # noqa: E402
from benchmark_stt_mac import word_error_rate as legacy_wer  # noqa: E402


def test_wer_is_the_reused_helper():
    """Gleicher Wertebegriff wie die Bestandsmessung (tests/test_wer.py)."""
    assert metrics.word_error_rate is legacy_wer
    assert metrics.word_error_rate("Hello, World!", "hello world") == 0.0
    assert metrics.word_error_rate("hello world", "hello earth") == 0.5


def test_visible_rates_perfect_and_none_cases():
    assert metrics.visible_error_rates("Hallo Welt.", "Hallo Welt.") == (0.0, 0.0)
    # Satzzeichen werden normalisiert weggestrichen: WER 0, aber sichtbar falsch.
    assert metrics.visible_error_rates("Hallo Welt.", "Hallo Welt") == (0.5, 0.0)
    # Nur Referenzwörter vorhanden: ohne Referenz nicht meßbar.
    assert metrics.visible_error_rates("", "Hallo") == (None, None)
    assert metrics.visible_error_rates("", "") == (None, None)


def test_visible_capitalization_error_rate():
    assert metrics.visible_error_rates("Hallo Welt", "hallo Welt") == (0.0, 0.5)
    # Deutsche Nomen, Umlaute und Satzzeichen in Anführungszeichen. Satzinitial
    # großgeschriebene Wörter zählen mit: "Ich" und "Kaffee" wurden kleingeschrieben.
    assert metrics.visible_error_rates("Ich möchte Kaffee", "ich möchte kaffee") \
        == (0.0, 2 / 3)
    assert metrics.visible_error_rates("„Hallo", "„hallo") == (0.0, 1.0)


def test_visible_counts_missing_and_extra_words_property():
    # Fehlendes Referenzwort verliert sein Satzzeichen und seine Großschreibung:
    assert metrics.visible_error_rates("Eins Zwei Drei.", "Eins Zwei") == (1 / 3, 1 / 3)
    # Fehlendes kleingeschriebenes Wort verliert nur sein Satzzeichen:
    assert metrics.visible_error_rates("eins zwei.", "eins zwei") == (0.5, 0.0)
    # Angehängtes Satzzeichen fehlt am gepaarten Wort:
    assert metrics.visible_error_rates("Eins, Zwei", "Eins Zwei") == (0.5, 0.0)
    # Zusätzliches großgeschriebenes Wort (normiert auf die Referenzwortzahl):
    assert metrics.visible_error_rates("eins zwei", "eins Vier zwei") == (0.0, 0.5)


def test_standalone_punctuation_tokens_are_not_counted():
    # Eigenständige Satzzeichen tragen kein Wort und werden nicht gezählt;
    # gezählt wird sichtbare Zeichensetzung an Wörtern (Dokumentation in metrics).
    assert metrics.visible_error_rates("Eins Zwei", "Eins , Zwei") == (0.0, 0.0)


def test_dictionary_hit_rate_whole_word_and_case():
    assert metrics.dictionary_hit_rate(["Welt", "Fahrrad"], "Hallo Welt.") == (1, 2)
    assert metrics.dictionary_hit_rate(["Welt"], "Weltall") == (0, 1)   # kein ganzes Wort
    assert metrics.dictionary_hit_rate(["Henrike"], "henrike") == (0, 1)  # Großschreibung zählt
    assert metrics.dictionary_hit_rate([], "egal") == (0, 0)
    assert metrics.dictionary_hit_rate(["E-Mail"], "die E-Mail ist da") == (1, 1)


def test_percentile_interpolates():
    assert metrics.percentile([], 95) is None
    assert metrics.percentile([5.0], 95) == 5.0
    assert metrics.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert metrics.percentile(list(range(101)), 95) == 95.0
    assert metrics.percentile(list(range(100)), 95) == 94.05


def test_summarize_times():
    empty = metrics.summarize_times([])
    assert empty == {"median": None, "p95": None, "min": None, "max": None}
    s = metrics.summarize_times([3.0, 1.0, 2.0])
    assert s["median"] == 2.0 and s["min"] == 1.0 and s["max"] == 3.0
    assert s["p95"] >= 2.0


def test_umlauts_stay_in_normalized_wer():
    assert metrics.word_error_rate("Ich möchte Kaffee", "ich mochte kaffee") == 1 / 3
