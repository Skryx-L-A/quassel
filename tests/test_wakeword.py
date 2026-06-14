"""Tests des Wake-Word-Freisprechens (normalize + match_wake + WakeListener).

Deterministisch: kein echtes Audio, keine echten Threads, keine Sleeps. Die
injizierten Callables sind einfache Fakes -- record_utterance() popt PCM-Bytes
aus einer Queue, transcribe() bildet Bytes auf Text ab, insert() haengt an eine
Liste an. run_once() wird direkt aufgerufen.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.wakeword import normalize, match_wake, WakeListener


# ----------------------------------------------------------------- Fakes
class FakeCfg:
    def __init__(self, enabled=True, phrase="Hey Quassel"):
        self.wakeword_enabled = enabled
        self.wakeword_phrase = phrase


def make_listener(cfg, utterances, mapping, busy=False):
    """Baut einen WakeListener mit Fake-Callables.

    utterances : Liste von PCM-Bytes, die record_utterance() der Reihe nach popt.
    mapping    : dict pcm_bytes -> transcript-str.
    busy       : fester Rueckgabewert von is_busy().
    """
    queue = list(utterances)
    inserted = []

    def record_utterance():
        return queue.pop(0) if queue else None

    def transcribe(pcm):
        return mapping.get(pcm)

    def insert(text):
        inserted.append(text)

    listener = WakeListener(cfg, record_utterance, transcribe, insert,
                            is_busy=(lambda: busy))
    return listener, inserted


# ----------------------------------------------------------------- normalize
def test_normalize_strips_punct_and_case():
    assert normalize("Hey, Quassel!") == "hey quassel"


def test_normalize_collapses_whitespace_and_keeps_umlauts():
    assert normalize("  Schöne   Grüße  ") == "schöne grüße"
    assert normalize("") == ""
    assert normalize(None) == ""


# ----------------------------------------------------------------- match_wake
def test_match_with_remainder():
    assert match_wake("Hey Quassel, schreib das hier", "Hey Quassel") \
        == (True, "schreib das hier")


def test_match_phrase_only_empty_remainder():
    assert match_wake("Hey Quassel", "Hey Quassel") == (True, "")


def test_match_whole_word_boundary():
    assert match_wake("heyquasselfoo bar", "Hey Quassel")[0] is False


def test_match_no_match():
    assert match_wake("schreib das", "Hey Quassel") == (False, "")


def test_match_tolerant_spacing_and_case():
    # Extra Leerzeichen + andere Schreibweise + nachgestelltes Satzzeichen.
    matched, rem = match_wake("hey   quassel.  mach mal", "Hey Quassel")
    assert matched is True
    assert rem == "mach mal", rem


# ----------------------------------------------------------------- run_once
def test_run_once_disabled_no_insert():
    cfg = FakeCfg(enabled=False)
    listener, inserted = make_listener(cfg, [b"a"], {b"a": "Hey Quassel mach das"})
    assert listener.run_once() is None
    assert inserted == []


def test_run_once_busy_no_insert():
    cfg = FakeCfg(enabled=True)
    listener, inserted = make_listener(cfg, [b"a"],
                                       {b"a": "Hey Quassel mach das"}, busy=True)
    assert listener.run_once() is None
    assert inserted == []


def test_run_once_one_shot():
    cfg = FakeCfg(enabled=True)
    listener, inserted = make_listener(cfg, [b"a"], {b"a": "Hey Quassel mach das"})
    out = listener.run_once()
    assert out == "mach das", out
    assert inserted == ["mach das"], inserted


def test_run_once_two_step():
    cfg = FakeCfg(enabled=True)
    listener, inserted = make_listener(
        cfg, [b"a", b"b"], {b"a": "Hey Quassel", b"b": "neuer text"})
    out = listener.run_once()
    assert out == "neuer text", out
    assert inserted == ["neuer text"], inserted


def test_run_once_non_match_no_insert():
    cfg = FakeCfg(enabled=True)
    listener, inserted = make_listener(cfg, [b"a"], {b"a": "irgendwas anderes"})
    assert listener.run_once() is None
    assert inserted == []


def test_run_once_no_audio_no_insert():
    cfg = FakeCfg(enabled=True)
    listener, inserted = make_listener(cfg, [], {})
    assert listener.run_once() is None
    assert inserted == []


def test_run_once_empty_transcript_no_insert():
    cfg = FakeCfg(enabled=True)
    listener, inserted = make_listener(cfg, [b"a"], {b"a": "   "})
    assert listener.run_once() is None
    assert inserted == []


def test_run_once_injected_exception_is_swallowed():
    # Ein Fehler im injizierten transcribe darf nicht nach aussen dringen.
    cfg = FakeCfg(enabled=True)
    inserted = []

    def record_utterance():
        return b"a"

    def transcribe(pcm):
        raise RuntimeError("boom")

    def insert(text):
        inserted.append(text)

    listener = WakeListener(cfg, record_utterance, transcribe, insert)
    assert listener.run_once() is None
    assert inserted == []


if __name__ == "__main__":
    tests = [
        test_normalize_strips_punct_and_case,
        test_normalize_collapses_whitespace_and_keeps_umlauts,
        test_match_with_remainder,
        test_match_phrase_only_empty_remainder,
        test_match_whole_word_boundary,
        test_match_no_match,
        test_match_tolerant_spacing_and_case,
        test_run_once_disabled_no_insert,
        test_run_once_busy_no_insert,
        test_run_once_one_shot,
        test_run_once_two_step,
        test_run_once_non_match_no_insert,
        test_run_once_no_audio_no_insert,
        test_run_once_empty_transcript_no_insert,
        test_run_once_injected_exception_is_swallowed,
    ]
    for fn in tests:
        fn()
        print("ok:", fn.__name__)
    print("ok")
