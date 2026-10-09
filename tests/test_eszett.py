"""ß-Wiederherstellung (quassel/eszett.py) und ihre Einstellung."""
import pytest

from quassel import dictation, eszett


@pytest.mark.parametrize("wrong,right", [
    ("grosses", "großes"), ("heisst", "heißt"), ("Strasse", "Straße"),
    ("Anschliessend", "Anschließend"), ("hinfliesst", "hinfließt"),
])
def test_restores_eszett_where_spelling_needs_it(wrong, right):
    assert eszett.restore_word(wrong) == right


@pytest.mark.parametrize("word", [
    "dass", "Masse", "Fluss", "lassen", "verlassen", "anpassen", "zusammenfassen",
    "class", "business", "Boss", "Business", "pass",
])
def test_leaves_valid_ss_words_and_english_alone(word):
    assert eszett.restore_word(word) == word


def test_restore_keeps_everything_but_the_words():
    text = "Das ist ein grosses Problem, dass er heisst wie die Strasse."
    assert eszett.restore(text) == "Das ist ein großes Problem, dass er heißt wie die Straße."


@pytest.mark.parametrize("setting,model,language,expected", [
    ("auto", "/m/ggml-turbo-german-q5_0.bin", "de", True),
    ("auto", "/m/ggml-large-v3-turbo-q5_0.bin", "de", False),
    ("auto", "/m/ggml-turbo-german-q5_0.bin", "en", False),
    ("on", "/m/ggml-large-v3-turbo-q5_0.bin", "mixed", True),
    ("off", "/m/ggml-turbo-german-q5_0.bin", "de", False),
])
def test_setting(setting, model, language, expected):
    assert eszett.enabled(setting, model, language) is expected


def test_dictation_applies_it_only_when_enabled(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(dictation.config, "read_serverenv",
                        lambda: {"MODEL_PATH": "/m/ggml-turbo-german-q5_0.bin"})
    cfg = SimpleNamespace(commands=False, punctuation=False, language="de", eszett="auto")
    assert dictation.postprocess_transcript("ein grosses Haus", cfg)[1] == "ein großes Haus"
    cfg.eszett = "off"
    assert dictation.postprocess_transcript("ein grosses Haus", cfg)[1] == "ein grosses Haus"
