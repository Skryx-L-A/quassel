"""VAD: zusammenhaengende Sprache statt kumulierter Klicks (Audit G98) und
Energieschwelle, die dem Grundrauschen folgt (gleiche Kalibrierung auf jeder
Plattform)."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.vad import RmsClassifier, SilenceDetector

RATE = 16000


def pcm(amplitude, seconds):
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *([amplitude] * n))


def detector(**kw):
    kw.setdefault("silence_rms", 350.0)
    kw.setdefault("min_speech_sec", 0.3)
    kw.setdefault("hang_sec", 1.2)
    return SilenceDetector(rate=RATE, frame_ms=30, **kw)


# ---------------------------------------------------------- Klicks addieren sich nicht
def test_scattered_clicks_never_count_as_speech():
    d = detector()
    for _ in range(20):                    # 20 Klicks a 60 ms, je 1 s Ruhe dazwischen
        d.feed(pcm(9000, 0.06))
        d.feed(pcm(0, 1.0))
    assert not d.speech_started, "20 Klicks (1,2 s kumuliert) sind keine Sprache"
    assert not d.stopped


def test_breathing_gap_inside_speech_is_bridged():
    d = detector()
    d.feed(pcm(8000, 0.15))
    d.feed(pcm(0, 0.09))                   # kurze Sprechpause
    d.feed(pcm(8000, 0.15))
    assert d.speech_started, "0,3 s Sprache mit 90 ms Pause ist ein Satz"


def test_long_gap_restarts_the_speech_run():
    d = detector()
    d.feed(pcm(8000, 0.2))
    d.feed(pcm(0, 0.4))                    # Pause ueber der Bruecke
    assert d.speech_run_sec == 0.0
    d.feed(pcm(8000, 0.2))
    assert not d.speech_started, "2 x 0,2 s getrennt sind keine 0,3 s am Stueck"


# ------------------------------------------------- nachlaufende Stille nach Sprechbeginn
def test_click_during_trailing_silence_does_not_extend_it():
    d = detector()
    d.feed(pcm(8000, 0.6))
    d.feed(pcm(0, 0.8))
    d.feed(pcm(9000, 0.03))                # ein Klick im Schweigen
    d.feed(pcm(0, 0.5))
    assert d.stopped, "ein Klick darf das Ende nicht hinausschieben"


def test_real_speech_resumes_and_resets_the_silence():
    d = detector()
    d.feed(pcm(8000, 0.6))
    d.feed(pcm(0, 0.9))
    d.feed(pcm(8000, 0.3))                 # der Sprecher setzt wieder an
    assert d.silence_sec == 0.0
    assert not d.stopped
    d.feed(pcm(0, 1.3))
    assert d.stopped


# ----------------------------------------------------------- adaptive Schwelle
def test_adaptive_threshold_follows_the_noise_floor():
    quiet = RmsClassifier()
    loud = RmsClassifier()
    for _ in range(20):
        quiet.is_speech(pcm(50, 0.03))
        loud.is_speech(pcm(700, 0.03))
    assert quiet.current_threshold() == 220.0           # Untergrenze
    assert abs(loud.current_threshold() - 700 * 2.2) < 1.0


def test_adaptive_threshold_is_capped_in_a_very_loud_room():
    c = RmsClassifier()
    for _ in range(30):
        c.is_speech(pcm(5000, 0.03))
    assert c.current_threshold() == c.ceiling


def test_adaptive_classifier_separates_speech_from_room_noise():
    noise = 600
    d = SilenceDetector(rate=RATE, classifier=RmsClassifier(), min_speech_sec=0.3,
                        hang_sec=1.0)
    d.feed(pcm(noise, 1.0))
    assert not d.speech_started, "gleichmaessiges Raumrauschen ist keine Sprache"
    d.feed(pcm(4000, 0.5))
    assert d.speech_started
    d.feed(pcm(noise, 1.2))
    assert d.stopped


def test_warmup_never_counts_as_speech():
    c = RmsClassifier(warmup_sec=0.3)
    results = [c.is_speech(pcm(8000, 0.03)) for _ in range(9)]
    assert not any(results[:9]), "die ersten 0,3 s kalibrieren nur"


def test_fixed_threshold_still_works():
    c = RmsClassifier(threshold=350.0)
    assert c.is_speech(pcm(500, 0.03))
    assert not c.is_speech(pcm(100, 0.03))


# ------------------------------------------------ eine Kalibrierung fuer alle Plattformen
def test_windows_has_no_calibration_of_its_own():
    """Die Wake-Aufnahme laeuft fuer Linux, macOS und Windows durch denselben
    Kern; win/app.py darf keine eigene Schwellenlogik tragen."""
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, "quassel", "win", "app.py"), encoding="utf-8") as f:
        text = f.read()
    for name in ("frame_rms", "SilenceDetector", "silence_rms"):
        assert name not in text, name
