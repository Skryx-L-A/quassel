"""Gelernte Wörter: Herkunft, Bestätigung, Verfall, Sperre.

Befund vom 09.10.: Die installierte App lernte "Jeff-Artigen" (Fehlhörung von
"Jev-artigen"), "Stand-Up-Runde" und "Marmora-Welt" nach je einem Vorkommen
in dictionary.txt. Von dort wirkten sie als Erkennungshilfe und hielten sich
selbst am Leben: Whisper schrieb die falsche Form wieder, weil sie im Prompt
stand. Gelernte Wörter liegen darum getrennt von den eigenen, mit Herkunft,
werden erst nach zwei getrennten Diktaten aktiv, verfallen und lassen sich
sperren.
"""
import json
import os

import pytest

from quassel import cli, config, learn

DAY = 86400
T0 = 1_760_000_000.0


# ------------------------------------------------------------ Erkennung

def test_hyphen_compounds_with_ordinary_parts_are_not_jargon():
    text = "Jeff-Artigen Modelle, die Stand-Up-Runde in der Marmora-Welt, E-Mail, T-Shirt"
    assert learn.jargon_words(text) == []


def test_hyphen_compounds_keep_only_their_jargon():
    text = "das PyTorch-Modell auf K8s-Clustern mit macOS-Version"
    assert learn.jargon_words(text) == ["PyTorch", "K8s", "macOS"]


def test_hyphenated_names_made_only_of_jargon_stay_whole():
    text = "GPT-4o, COVID-19, Qwen3-4B, USB-C und x86-64"
    assert learn.jargon_words(text) == ["GPT-4o", "COVID-19", "Qwen3-4B", "USB-C", "x86-64"]


# ------------------------------------------------------------ Lebenszyklus

def test_one_dictation_never_activates_a_word():
    state = learn.empty_state()
    assert learn.observe(state, ["PyTorch"], T0) == []
    # Dasselbe Wort mehrfach in kurzer Folge (ein Gespräch, eine Fehlhörung):
    assert learn.observe(state, ["PyTorch"], T0 + 60) == []
    assert learn.observe(state, ["PyTorch"], T0 + 300) == []
    assert learn.active_words(state, T0 + 300) == []


def test_a_second_dictation_later_activates_it():
    state = learn.empty_state()
    learn.observe(state, ["PyTorch"], T0)
    assert learn.observe(state, ["PyTorch"], T0 + learn.MIN_GAP_S) == ["PyTorch"]
    assert learn.active_words(state, T0 + learn.MIN_GAP_S) == ["PyTorch"]
    entry = learn.entry(state, "pytorch")
    assert entry["first"] == T0 and entry["seen"] == 2


def test_frequent_short_gaps_still_count_after_the_gap():
    state = learn.empty_state()
    for minute in range(0, 13, 3):           # alle 3 Minuten, 12 Minuten lang
        learn.observe(state, ["PyTorch"], T0 + minute * 60)
    assert learn.active_words(state, T0 + 12 * 60) == ["PyTorch"]


def test_unused_word_falls_out():
    state = learn.empty_state()
    learn.observe(state, ["PyTorch"], T0)
    learn.observe(state, ["PyTorch"], T0 + 3600)
    later = T0 + 3600 + (learn.UNUSED_DAYS + 1) * DAY
    assert learn.active_words(state, later) == []
    learn.prune(state, later)
    assert learn.entry(state, "PyTorch") is None


def test_self_confirming_word_expires_unless_kept():
    """Ein gelerntes Wort im Prompt erzeugt sich selbst wieder. Darum endet
    jedes gelernte Wort spätestens nach MAX_ACTIVE_DAYS, auch wenn es weiter
    vorkommt; behalten heißt: ins eigene Wörterbuch übernehmen."""
    state = learn.empty_state()
    learn.observe(state, ["PyTorch"], T0)
    learn.observe(state, ["PyTorch"], T0 + 3600)
    t = T0 + 3600
    while t < T0 + (learn.MAX_ACTIVE_DAYS + 2) * DAY:
        t += DAY
        learn.observe(state, ["PyTorch"], t)
        learn.prune(state, t)
    assert "PyTorch" not in learn.active_words(state, t)


def test_forgotten_word_never_comes_back():
    state = learn.empty_state()
    learn.observe(state, ["Jeff"], T0)
    learn.observe(state, ["Jeff"], T0 + 3600)
    assert learn.forget(state, "jeff") is True
    for day in range(1, 5):
        assert learn.observe(state, ["Jeff", "JEFF"], T0 + day * DAY) == []
    assert learn.active_words(state, T0 + 5 * DAY) == []


def test_manual_and_extra_blocked_words_are_not_tracked():
    state = learn.empty_state()
    learn.observe(state, ["PyTorch", "NASA"], T0, manual=["pytorch"], blocked=["nasa"])
    assert learn.entry(state, "PyTorch") is None and learn.entry(state, "NASA") is None


def test_damaged_state_starts_empty():
    for raw in (None, [], {"words": "x"}, {"words": {"A": "bad"}, "blocked": 3}):
        state = learn.normalize_state(raw)
        assert state["words"] == {} and state["blocked"] == []


# ------------------------------------------------------------ Ablage

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "DICTIONARY", str(tmp_path / "dictionary.txt"))
    monkeypatch.setattr(config, "LEARNED", str(tmp_path / "dictionary-learned.json"))
    monkeypatch.setattr(config, "REPLACEMENTS", str(tmp_path / "replacements.txt"))
    monkeypatch.setattr(config, "CLEANUP_CORRECTIONS",
                        str(tmp_path / "cleanup-corrections.txt"))
    config._dictionary_cache["key"] = None
    config._learned_cache["key"] = None
    return tmp_path


def _learn_twice(text):
    config.dictionary_learn(text, now=T0)
    return config.dictionary_learn(text, now=T0 + 3600)


def test_learned_words_stay_out_of_the_own_dictionary(store):
    config.dictionary_save("Alpha")
    assert _learn_twice("Ich nutze PyTorch") == ["PyTorch"]
    assert config.dictionary_manual() == ["Alpha"]
    with open(config.DICTIONARY, encoding="utf-8") as f:
        assert f.read() == "Alpha\n"
    assert config.dictionary_words(now=T0 + 3600) == ["Alpha", "PyTorch"]
    assert oct(os.stat(config.LEARNED).st_mode & 0o777) == "0o600"


def test_learned_list_shows_origin(store):
    _learn_twice("Ich nutze PyTorch")
    [item] = config.dictionary_learned(now=T0 + 3600)
    assert item["word"] == "PyTorch" and item["first"] == T0 and item["seen"] == 2


def test_forget_removes_and_blocks(store):
    _learn_twice("Ich nutze PyTorch")
    assert config.dictionary_forget("PyTorch") is True
    assert config.dictionary_words(now=T0 + 3600) == []
    assert config.dictionary_learn("PyTorch", now=T0 + 2 * DAY) == []
    assert config.dictionary_learn("PyTorch", now=T0 + 3 * DAY) == []
    assert config.dictionary_words(now=T0 + 3 * DAY) == []


def test_keep_moves_into_the_own_dictionary(store):
    _learn_twice("Ich nutze PyTorch")
    assert config.dictionary_keep("PyTorch") is True
    assert config.dictionary_manual() == ["PyTorch"]
    assert config.dictionary_learned(now=T0 + 3600) == []
    far = T0 + (learn.MAX_ACTIVE_DAYS + 10) * DAY
    assert config.dictionary_words(now=far) == ["PyTorch"]


def test_a_correction_overrides_learning(store):
    """Steht eine Schreibweise als Auslöser in den Textersetzungen oder den
    Korrekturen der Bereinigung, ist sie als falsch bekannt."""
    config.replacement_save("JEFF=Jev")
    with open(config.CLEANUP_CORRECTIONS, "w", encoding="utf-8") as f:
        f.write("QWENN=Qwen\n")
    assert _learn_twice("JEFF und QWENN und NASA") == ["NASA"]


def test_old_learned_entries_in_the_file_are_not_touched(store):
    config.dictionary_save("Eigenname")
    _learn_twice("PyTorch")
    config.dictionary_save("Eigenname\nNeu")         # Kontrollzentrum speichert
    assert config.dictionary_words(now=T0 + 3600) == ["Eigenname", "Neu", "PyTorch"]


def test_damaged_learned_file_is_ignored(store):
    with open(config.LEARNED, "w", encoding="utf-8") as f:
        f.write("{kaputt")
    config.dictionary_save("Alpha")
    assert config.dictionary_words(now=T0) == ["Alpha"]
    assert _learn_twice("PyTorch") == ["PyTorch"]
    json.loads(open(config.LEARNED, encoding="utf-8").read())


# ------------------------------------------------------------ CLI

def test_cli_add_does_not_copy_learned_words_into_the_file(store):
    _learn_twice("PyTorch")
    assert cli._dict_add("Alpha") == 0
    assert config.dictionary_manual() == ["Alpha"]


def test_cli_remove_of_a_learned_word_blocks_it(store, monkeypatch):
    monkeypatch.setattr(config, "_now", lambda: T0 + 3600)
    _learn_twice("PyTorch")
    assert cli._dict_remove("PyTorch") == 0
    assert config.dictionary_words(now=T0 + 3600) == []
    assert config.dictionary_learn("PyTorch", now=T0 + 5 * DAY) == []


def test_the_store_stays_small_and_keeps_active_words_first():
    state = learn.empty_state()
    learn.observe(state, ["Aktiv1"], T0)
    learn.observe(state, ["Aktiv1"], T0 + 3600)
    learn.observe(state, ["Kand%d" % i for i in range(learn.MAX_ENTRIES + 5)], T0 + 7200)
    learn.prune(state, T0 + 7200)
    assert len(state["words"]) == learn.MAX_ENTRIES
    assert learn.active_words(state, T0 + 7200) == ["Aktiv1"]


def test_short_capital_fragments_of_compounds_are_not_learned():
    """Aus dem Wörterbuch auf llpc: "ER-Co-Op-Start-Option", "OX-Alpha-Worker",
    "MD-File". Zwei Großbuchstaben aus einem Kompositum sind fast nie der Name,
    um den es ging."""
    assert learn.jargon_words("ER-Co-Op-Start-Option OX-Alpha-Worker MD-File") == []
    assert learn.jargon_words("PDF-Variante K8-Cluster") == ["PDF", "K8"]
