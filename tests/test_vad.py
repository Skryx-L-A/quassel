"""Tests der Energie-VAD (frame_rms + SilenceDetector)."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.vad import frame_rms, SilenceDetector

RATE = 16000


def pcm(amplitude, seconds, rate=RATE):
    """s16le-Mono-PCM aus konstanten Samples (gleichgerichtete Amplitude)."""
    n = int(rate * seconds)
    return struct.pack(f"<{n}h", *([amplitude] * n))


def test_frame_rms_loud_vs_quiet():
    quiet = pcm(0, 0.1)
    loud = pcm(8000, 0.1)
    assert frame_rms(loud) > frame_rms(quiet), (frame_rms(loud), frame_rms(quiet))
    assert frame_rms(b"") == 0.0
    assert frame_rms(b"\x00") == 0.0          # ungerades Byte -> robust
    # Konstante Amplitude -> RMS gleich Betrag der Amplitude.
    assert abs(frame_rms(loud) - 8000.0) < 1.0, frame_rms(loud)


def test_fresh_detector_idle():
    d = SilenceDetector()
    assert not d.speech_started
    assert not d.stopped
    assert d.silence_sec == 0.0


def test_speech_then_silence_stops():
    d = SilenceDetector(rate=RATE, silence_rms=350.0, min_speech_sec=0.3,
                        hang_sec=1.2, frame_ms=30)
    d.feed(pcm(8000, 0.5))                    # ~0,5 s laut
    assert d.speech_started, "Sprache haette beginnen muessen"
    assert not d.stopped, "Stille noch zu kurz"
    d.feed(pcm(0, 1.5))                        # ~1,5 s leise
    assert d.stopped, "nachlaufende Stille haette stoppen muessen"
    assert d.silence_sec >= 1.2, d.silence_sec


def test_quiet_only_never_stops():
    d = SilenceDetector()
    d.feed(pcm(0, 3.0))                        # nur Stille
    assert not d.speech_started
    assert not d.stopped


def test_chunked_feed_matches():
    # Stueckelung darf das Ergebnis nicht veraendern (interner Rest-Puffer).
    d = SilenceDetector()
    blob = pcm(8000, 0.5) + pcm(0, 1.5)
    step = 777                                # krumme Stueckgroesse
    for i in range(0, len(blob), step):
        d.feed(blob[i:i + step])
    assert d.speech_started and d.stopped


def test_reset_clears_state():
    d = SilenceDetector()
    d.feed(pcm(8000, 0.5))
    d.feed(pcm(0, 1.5))
    assert d.speech_started and d.stopped
    d.reset()
    assert not d.speech_started
    assert not d.stopped
    assert d.silence_sec == 0.0


if __name__ == "__main__":
    for fn in [test_frame_rms_loud_vs_quiet, test_fresh_detector_idle,
               test_speech_then_silence_stops, test_quiet_only_never_stops,
               test_chunked_feed_matches, test_reset_clears_state]:
        fn()
        print("ok:", fn.__name__)
    print("ok")
