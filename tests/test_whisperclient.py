"""Tests des curl-Argumentbaus für /inference (Sprache auto/mixed/fest, Prompt)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import whisperclient as wc


class Cfg:
    def __init__(self, language):
        self.language = language


def _joined(args):
    return " ".join(args)


def test_auto_has_no_language_field():
    args = wc.build_inference_args("a.wav", Cfg("auto"), [])
    assert "language=auto" not in _joined(args)
    assert not any(a.startswith("language=") for a in args)
    assert "prompt=" not in _joined(args)              # keine Wörter -> kein Prompt


def test_fixed_language_sets_field():
    args = wc.build_inference_args("a.wav", Cfg("de"), [])
    assert "language=de" in args


def test_mixed_adds_primer_and_no_hard_language():
    args = wc.build_inference_args("a.wav", Cfg("mixed"), [])
    assert not any(a.startswith("language=") for a in args)   # auto-Erkennung
    prompt = next(a for a in args if a.startswith("prompt="))
    assert wc.MIXED_PRIMER in prompt


def test_dictionary_words_join_into_prompt():
    args = wc.build_inference_args("a.wav", Cfg("auto"), ["PyTorch", "NASA"])
    prompt = next(a for a in args if a.startswith("prompt="))
    assert "PyTorch" in prompt and "NASA" in prompt


def test_mixed_combines_primer_and_words():
    args = wc.build_inference_args("a.wav", Cfg("mixed"), ["Kubernetes"])
    prompt = next(a for a in args if a.startswith("prompt="))
    assert wc.MIXED_PRIMER in prompt and "Kubernetes" in prompt


if __name__ == "__main__":
    for fn in [test_auto_has_no_language_field, test_fixed_language_sets_field,
               test_mixed_adds_primer_and_no_hard_language,
               test_dictionary_words_join_into_prompt, test_mixed_combines_primer_and_words]:
        fn(); print("ok:", fn.__name__)
    print("ALL WHISPERCLIENT TESTS PASSED")
