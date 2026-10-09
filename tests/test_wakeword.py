"""Tests des Wake-Word-Freisprechens (normalize + match_wake + WakeListener).

Deterministisch: kein echtes Audio, keine echten Threads, keine Sleeps. Die
injizierten Callables sind einfache Fakes -- record_utterance() popt PCM-Bytes
aus einer Queue, transcribe() bildet Bytes auf Text ab, insert() haengt an eine
Liste an. run_once() wird direkt aufgerufen.
"""
import sys, os
import threading
import time
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import config
from quassel.wakeword import normalize, match_wake, WakeListener, InsertGate


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


def test_normalize_keeps_accented_and_other_letters_as_one_word():
    # Akzente, Trema, polnisches ł und kombinierende Zeichen bleiben im Wort;
    # ß bleibt ß (lower, nicht casefold).
    assert normalize("Café, naïve Łódź!") == "café naïve łódź"
    assert normalize("Straße") == "straße"
    assert normalize("Café Quassel") == "café quassel"
    assert normalize("नमस्ते Quassel") == "नमस्ते quassel"


def test_match_remainder_is_cut_after_the_phrase_with_non_ascii_words():
    # Wortzaehlung von normalize und Restschnitt muessen dieselben Woerter sehen.
    assert match_wake("Hé Quassel, schreib Grüße", "Hé Quassel") \
        == (True, "schreib Grüße")
    assert match_wake("Hey Quaßel, naïve Idee", "Hey Quaßel") \
        == (True, "naïve Idee")
    assert match_wake("Okay Łukasz, mach weiter", "Okay Łukasz") \
        == (True, "mach weiter")
    assert match_wake("Ça va Quassel: écris ça", "Ça va Quassel") \
        == (True, "écris ça")
    assert match_wake("Hey naïve Quassel, schreib Grüße", "Hey naïve Quassel") \
        == (True, "schreib Grüße")
    assert match_wake("Hej Łódź, pisz dalej", "Hej Łódź") \
        == (True, "pisz dalej")


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


def test_match_fuzzy_mishearing():
    # Whisper verhoert das Kunstwort oft ("Hey Kassel"): unscharfer Treffer.
    matched, rem = match_wake("Hey Kassel, mach das", "Hey Quassel")
    assert matched is True
    assert rem == "mach das", rem
    # klar Unaehnliches loest weiterhin NICHT aus.
    assert match_wake("irgendwas anderes", "Hey Quassel")[0] is False


def test_match_tolerant_spacing_and_case():
    # Extra Leerzeichen + andere Schreibweise + nachgestelltes Satzzeichen.
    matched, rem = match_wake("hey   quassel.  mach mal", "Hey Quassel")
    assert matched is True
    assert rem == "mach mal", rem


def test_match_sound_alikes_of_the_made_up_word():
    # Audit G97: das Kunstwort wird so geschrieben, wie es klingt.
    for heard in ("Hey Kassel", "Hey Castle", "He Quassel", "Hey Kwassel",
                  "Hey, Quasel", "Hey Quassl"):
        assert match_wake(heard + ", mach das", "Hey Quassel") == (True, "mach das"), heard


def test_match_joined_and_split_words():
    assert match_wake("Heykassel mach das", "Hey Quassel") == (True, "mach das")
    assert match_wake("Hey Quas sel mach das", "Hey Quassel") == (True, "mach das")


def test_match_allows_one_filler_word_in_front():
    assert match_wake("Ok, hey Quassel, schreib das", "Hey Quassel") == (True, "schreib das")
    assert match_wake("Ähm Hey Quassel", "Hey Quassel") == (True, "")
    # ein inhaltliches Wort davor ist kein Fuellwort
    assert match_wake("Schreibe hey Quassel", "Hey Quassel")[0] is False


def test_match_rejects_other_words_that_only_share_letters():
    for heard in ("Hey Russel mach das", "Kassel", "hey",
                  "heyquasselfoo bar", "Hey Mustermann"):
        assert match_wake(heard, "Hey Quassel")[0] is False, heard


def test_match_short_phrases_are_stricter():
    assert match_wake("Max, mach das", "Max") == (True, "mach das")
    assert match_wake("Mach das", "Max")[0] is False


def test_match_remainder_keeps_the_original_text_after_a_joined_word():
    assert match_wake("Heykassel: Mach DAS, bitte.", "Hey Quassel") \
        == (True, "Mach DAS, bitte.")


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


# ----------------------------------------------------------- Einfuege-Sperre (G97)
def test_wake_insert_is_dropped_when_dictation_started_after_recording():
    """is_busy wurde frueher nur VOR der Aufnahme geprueft: ein inzwischen
    gestartetes Tastatur-Diktat bekam das Wake-Einfuegen dazwischen."""
    state = {"busy": False}
    inserted = []
    listener = WakeListener(
        FakeCfg(), lambda: b"a", lambda pcm: "Hey Quassel mach das",
        inserted.append, is_busy=lambda: state["busy"], gate=InsertGate())
    real_transcribe = listener._transcribe

    def transcribe(pcm):
        text = real_transcribe(pcm)
        state["busy"] = True           # Hotkey gedrueckt, waehrend Whisper rechnete
        return text

    listener._transcribe = transcribe
    assert listener.run_once() is None
    assert inserted == []


def test_gate_refuses_a_second_wake_insert_and_a_wake_insert_during_dictation():
    gate = InsertGate()
    assert gate.enter_wake() is True
    assert gate.wake_inserting
    other = []
    t = threading.Thread(target=lambda: other.append(gate.enter_wake()))
    t.start()
    t.join()
    assert other == [False]
    gate.exit_wake()
    assert not gate.wake_inserting
    with gate.dictation():
        assert gate.enter_wake() is False
    assert gate.enter_wake() is True


def test_dictation_waits_for_a_running_wake_insert_then_goes_on():
    gate = InsertGate()
    assert gate.enter_wake()
    order = []

    def keyboard():
        with gate.dictation(timeout=2.0):
            order.append("dictation")

    t = threading.Thread(target=keyboard)
    t.start()
    time.sleep(0.1)
    assert order == []                    # wartet auf das Wake-Einfuegen
    order.append("wake done")
    gate.exit_wake()
    t.join(2)
    assert order == ["wake done", "dictation"]


def test_dictation_never_blocks_for_good():
    gate = InsertGate()
    assert gate.enter_wake()
    done = []

    def keyboard():
        with gate.dictation(timeout=0.05):
            done.append(1)

    t = threading.Thread(target=keyboard)
    t.start()
    t.join(2)
    assert done == [1]


def test_wake_thread_may_pass_through_dictation():
    """Wake und Tastatur teilen dictation._insert: der Wake-Thread darf durch."""
    gate = InsertGate()
    assert gate.enter_wake()
    with gate.dictation(timeout=0.05):
        pass
    gate.exit_wake()


def test_followup_recording_uses_its_own_callable():
    calls = []
    listener = WakeListener(
        FakeCfg(), lambda: calls.append("first") or b"a",
        lambda pcm: {b"a": "Hey Quassel", b"b": "naechster Satz"}[pcm],
        calls.append, record_followup=lambda: calls.append("followup") or b"b",
        gate=InsertGate())
    assert listener.run_once() == "naechster Satz"
    assert calls == ["first", "followup", "naechster Satz"]


def test_spotter_capture_is_verified_by_default_and_trusted_when_verify_is_off(
        tmp_path, monkeypatch):
    ini = tmp_path / "config.ini"
    monkeypatch.setattr(config, "CONFIG", str(ini))
    for verify, expected in (("true", []), ("false", ["schreib das"])):
        ini.write_text("[wakeword]\nverify = %s\n" % verify, encoding="utf-8")
        inserted = []
        listener = WakeListener(
            FakeCfg(), lambda: b"a", lambda pcm: "schreib das", inserted.append,
            gate=InsertGate(), trust_capture=True)
        listener.run_once()
        assert inserted == expected, (verify, inserted)
    # ohne Spotter (trust_capture False) gilt immer die Phrasenpruefung
    ini.write_text("[wakeword]\nverify = false\n", encoding="utf-8")
    inserted = []
    WakeListener(FakeCfg(), lambda: b"a", lambda pcm: "schreib das", inserted.append,
                 gate=InsertGate()).run_once()
    assert inserted == []


if __name__ == "__main__":
    tests = [
        test_normalize_strips_punct_and_case,
        test_normalize_collapses_whitespace_and_keeps_umlauts,
        test_match_with_remainder,
        test_match_phrase_only_empty_remainder,
        test_match_whole_word_boundary,
        test_match_no_match,
        test_match_fuzzy_mishearing,
        test_match_tolerant_spacing_and_case,
        test_match_sound_alikes_of_the_made_up_word,
        test_match_joined_and_split_words,
        test_match_allows_one_filler_word_in_front,
        test_match_rejects_other_words_that_only_share_letters,
        test_match_short_phrases_are_stricter,
        test_match_remainder_keeps_the_original_text_after_a_joined_word,
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
