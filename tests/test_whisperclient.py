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


def test_ensure_server_returns_at_once_when_up(monkeypatch):
    started = []
    monkeypatch.setattr(wc, "server_up", lambda timeout=2: True)
    monkeypatch.setattr(wc, "STARTER", lambda: started.append(1))
    assert wc.ensure_server(deadline=0.1) is True
    assert started == []                       # läuft schon -> nichts starten


def test_ensure_server_gives_up_after_the_deadline(monkeypatch):
    """Die Frist ist eine Wanduhr-Frist: der Aufrufer am Diktat-Ende darf
    nicht zwei Minuten warten, nur weil der Server nicht hochkommt."""
    import time
    monkeypatch.setattr(wc, "server_up", lambda timeout=2: False)
    monkeypatch.setattr(wc, "STARTER", lambda: None)
    t0 = time.monotonic()
    assert wc.ensure_server(deadline=0.2) is False
    assert time.monotonic() - t0 < 5


if __name__ == "__main__":
    for fn in [test_auto_has_no_language_field, test_fixed_language_sets_field,
               test_mixed_adds_primer_and_no_hard_language,
               test_dictionary_words_join_into_prompt, test_mixed_combines_primer_and_words]:
        fn(); print("ok:", fn.__name__)
    print("ALL WHISPERCLIENT TESTS PASSED")
