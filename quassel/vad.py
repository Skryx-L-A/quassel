"""Energie-basierte Sprachaktivitaetserkennung (Voice Activity Detection).

Arbeitet auf rohem PCM im Quassel-Aufnahmeformat: 16 kHz, mono, signed
16-bit little-endian (s16le). Es wird bewusst nur die Standardbibliothek
benutzt (struct/math), damit das Modul ohne numpy o. ae. laeuft.

Idee: Das Signal wird in kurze Rahmen (frames) zerlegt. Ein Rahmen gilt als
"Sprache", wenn seine RMS-Amplitude einen Schwellwert erreicht. Der
SilenceDetector merkt sich, ob ueberhaupt schon genug Sprache kam, und misst
danach die nachlaufende Stille -- ist sie lang genug, gilt die Aeusserung als
beendet.
"""
import math
import struct

SAMPLE_BYTES = 2  # s16le -> 2 Byte pro Sample


def frame_rms(pcm_bytes):
    """RMS-Amplitude (0.0 .. ~32768) eines s16le-PCM-Stuecks.

    Liefert 0.0 fuer leere Eingabe. Ein ungerades letztes Byte (halbes
    Sample) wird ignoriert, damit auch unsauber geschnittene Puffer nicht
    zum Absturz fuehren.
    """
    if not pcm_bytes:
        return 0.0
    usable = len(pcm_bytes) - (len(pcm_bytes) % SAMPLE_BYTES)
    if usable < SAMPLE_BYTES:
        return 0.0
    n = usable // SAMPLE_BYTES
    samples = struct.unpack(f"<{n}h", pcm_bytes[:usable])
    acc = 0
    for s in samples:
        acc += s * s
    return math.sqrt(acc / n)


class SilenceDetector:
    """Erkennt: Sprache hat begonnen, danach genug nachlaufende Stille zum Stoppen.

    PCM-Stuecke beliebiger Laenge werden ueber feed() eingespeist; der Zustand
    wird ueber die Eigenschaften abgefragt. Reststuecke zwischen den feed()-
    Aufrufen werden intern zwischengespeichert, sodass Rahmen unabhaengig von
    der Aufruf-Granularitaet immer gleich gross sind.
    """

    def __init__(self, rate=16000, silence_rms=350.0, min_speech_sec=0.3,
                 hang_sec=1.2, frame_ms=30):
        self.rate = rate
        self.silence_rms = silence_rms
        self.min_speech_sec = min_speech_sec
        self.hang_sec = hang_sec
        self.frame_ms = frame_ms
        # Samples pro Rahmen (mind. 1), daraus die Byte-Groesse des Rahmens.
        self._frame_samples = max(1, int(rate * frame_ms / 1000))
        self._frame_bytes = self._frame_samples * SAMPLE_BYTES
        self._frame_sec = self._frame_samples / float(rate)
        self.reset()

    def reset(self):
        """Setzt den Detektor in den Ausgangszustand zurueck."""
        self._buf = b""
        self._speech_sec = 0.0      # bisher gesehene Sprachdauer (kumuliert)
        self._silence_sec = 0.0     # aktuelle nachlaufende Stille
        self._speech_started = False
        self._stopped = False

    def feed(self, pcm_bytes):
        """Speist ein PCM-Stueck ein und aktualisiert den Zustand."""
        if not pcm_bytes:
            return
        self._buf += pcm_bytes
        # So viele volle Rahmen verarbeiten, wie der Puffer hergibt.
        while len(self._buf) >= self._frame_bytes:
            frame = self._buf[:self._frame_bytes]
            self._buf = self._buf[self._frame_bytes:]
            self._process_frame(frame)

    def _process_frame(self, frame):
        is_speech = frame_rms(frame) >= self.silence_rms
        if is_speech:
            # Jeder Sprachrahmen verlaengert die Sprachdauer und setzt die
            # nachlaufende Stille zurueck.
            self._speech_sec += self._frame_sec
            self._silence_sec = 0.0
            if not self._speech_started and self._speech_sec >= self.min_speech_sec:
                self._speech_started = True
        elif self._speech_started:
            # Stille zaehlt erst nach Sprechbeginn als "nachlaufend".
            self._silence_sec += self._frame_sec
            if self._silence_sec >= self.hang_sec:
                self._stopped = True

    @property
    def speech_started(self):
        """True, sobald mindestens min_speech_sec an Sprache gesehen wurde."""
        return self._speech_started

    @property
    def stopped(self):
        """True, sobald nach Sprechbeginn die nachlaufende Stille hang_sec erreicht."""
        return self._stopped

    @property
    def silence_sec(self):
        """Aktuelle Dauer der nachlaufenden Stille in Sekunden."""
        return self._silence_sec
