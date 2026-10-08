"""Sprachaktivitaetserkennung (Voice Activity Detection) fuer Quassel.

Arbeitet auf rohem PCM im Quassel-Aufnahmeformat: 16 kHz, mono, signed
16-bit little-endian (s16le). Dieses Modul braucht nur die Standardbibliothek
(struct/math), damit es ohne numpy o. ae. laeuft; das optionale Silero-Backend
steht in vad_silero.py und wird erst geladen, wenn eine Funktion es braucht.

Aufbau:
  * Ein Klassifikator beurteilt einen Rahmen (frame) als Sprache oder nicht.
    RmsClassifier ist die Energieschwelle (immer vorhanden, auch adaptiv am
    Grundrauschen), SileroVad (vad_silero.py) das neuronale Backend.
  * Der SilenceDetector zaehlt ZUSAMMENHAENGENDE Sprache (kurze Luecken werden
    ueberbrueckt, einzelne Klicks addieren sich nicht auf), merkt sich, ob
    Sprache begonnen hat, und misst danach die nachlaufende Stille.
"""
import configparser
import math
import struct

SAMPLE_BYTES = 2  # s16le -> 2 Byte pro Sample

# Vorgaben der Energieschwelle. FLOOR schuetzt vor einer Schwelle im
# Eigenrauschen des Mikrofons, CEILING davor, dass ein lautes Umfeld die
# Schwelle ueber normale Sprache hebt.
RMS_FLOOR = 220.0
RMS_FACTOR = 2.2
RMS_CEILING = 2500.0

AUTOSTOP_SILENCE_DEFAULT = 1.5   # s Stille bis zum Auto-Stopp
AUTOSTOP_SILENCE_RANGE = (0.4, 30.0)


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


class RmsClassifier:
    """Energie-Klassifikator: Rahmen ist Sprache, wenn sein RMS die Schwelle erreicht.

    ``threshold`` gesetzt: feste Schwelle. ``threshold=None``: die Schwelle folgt
    dem Grundrauschen. Dazu merkt sich der Klassifikator die RMS-Werte der
    letzten ``history_sec`` Sekunden, nimmt deren unteres Dezil als Rauschpegel
    und setzt die Schwelle auf ``factor`` mal Rauschpegel (zwischen ``floor``
    und ``ceiling``). Die ersten ``warmup_sec`` zaehlen nie als Sprache, sie
    kalibrieren nur; spaeter sinkt die Schwelle mit, sobald Pausen kommen.
    Das gilt auf jeder Plattform gleich -- es gibt keine eigene Kalibrierung.
    """

    def __init__(self, threshold=None, rate=16000, frame_ms=30, floor=RMS_FLOOR,
                 factor=RMS_FACTOR, ceiling=RMS_CEILING, warmup_sec=0.3,
                 history_sec=5.0):
        self.threshold = threshold
        self.frame_samples = max(1, int(rate * frame_ms / 1000))
        self.floor, self.factor, self.ceiling = floor, factor, ceiling
        frame_sec = self.frame_samples / float(rate)
        self._warmup = max(0, int(round(warmup_sec / frame_sec)))
        self._history = max(1, int(round(history_sec / frame_sec)))
        self.reset()

    def reset(self):
        self._levels = []
        self._seen = 0

    def current_threshold(self):
        """Aktuell geltende Schwelle (fest oder aus dem Grundrauschen)."""
        if self.threshold is not None:
            return self.threshold
        if not self._levels:
            return self.floor
        ordered = sorted(self._levels)
        noise = ordered[len(ordered) // 10]
        return min(self.ceiling, max(self.floor, noise * self.factor))

    def is_speech(self, frame):
        level = frame_rms(frame)
        if self.threshold is not None:
            return level >= self.threshold
        self._levels.append(level)
        if len(self._levels) > self._history:
            del self._levels[0]
        self._seen += 1
        if self._seen <= self._warmup:
            return False
        return level >= self.current_threshold()


class SilenceDetector:
    """Erkennt: Sprache hat begonnen, danach genug nachlaufende Stille zum Stoppen.

    PCM-Stuecke beliebiger Laenge werden ueber feed() eingespeist; der Zustand
    wird ueber die Eigenschaften abgefragt. Reststuecke zwischen den feed()-
    Aufrufen werden intern zwischengespeichert, sodass Rahmen unabhaengig von
    der Aufruf-Granularitaet immer gleich gross sind.

    Sprache zaehlt nur ZUSAMMENHAENGEND (Audit G98): ein Sprachabschnitt
    ueberlebt Luecken bis ``bridge_sec`` (Atempausen, leise Konsonanten), wird
    aber bei laengerer Stille auf null gesetzt. Einzelne Klicks und Knackser
    ueber Sekunden verteilt summieren sich dadurch nicht mehr zu "Sprache".
    Nach Sprechbeginn setzt erst ein Sprachabschnitt von ``resume_sec`` die
    nachlaufende Stille zurueck -- ein Klick im Schweigen verlaengert sie nicht.

    ``classifier`` (siehe RmsClassifier, vad_silero.SileroVad) legt Rahmengroesse
    und Entscheidung fest; ohne ihn gilt die feste Energieschwelle ``silence_rms``.
    """

    def __init__(self, rate=16000, silence_rms=350.0, min_speech_sec=0.3,
                 hang_sec=1.2, frame_ms=30, classifier=None, bridge_sec=0.12,
                 resume_sec=0.09):
        self.rate = rate
        self.silence_rms = silence_rms
        self.min_speech_sec = min_speech_sec
        self.hang_sec = hang_sec
        self.frame_ms = frame_ms
        self.bridge_sec = bridge_sec
        self.resume_sec = resume_sec
        if classifier is None:
            classifier = RmsClassifier(threshold=silence_rms, rate=rate,
                                       frame_ms=frame_ms)
        self.classifier = classifier
        # Samples pro Rahmen (mind. 1), daraus die Byte-Groesse des Rahmens.
        self._frame_samples = max(1, int(classifier.frame_samples))
        self._frame_bytes = self._frame_samples * SAMPLE_BYTES
        self._frame_sec = self._frame_samples / float(rate)
        self.reset()

    def reset(self):
        """Setzt den Detektor in den Ausgangszustand zurueck."""
        self._buf = b""
        self._run_sec = 0.0         # aktueller zusammenhaengender Sprachabschnitt
        self._gap_sec = 0.0         # Luecke seit dem letzten Sprachrahmen
        self._silence_sec = 0.0     # aktuelle nachlaufende Stille
        self._speech_started = False
        self._stopped = False
        reset = getattr(self.classifier, "reset", None)
        if reset is not None:
            reset()

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
        if self.classifier.is_speech(frame):
            self._run_sec += self._frame_sec
            self._gap_sec = 0.0
            if not self._speech_started:
                if self._run_sec >= self.min_speech_sec:
                    self._speech_started = True
            elif self._run_sec >= self.resume_sec:
                self._silence_sec = 0.0
            return
        self._gap_sec += self._frame_sec
        if self._gap_sec > self.bridge_sec:
            self._run_sec = 0.0
        if self._speech_started:
            # Stille zaehlt erst nach Sprechbeginn als "nachlaufend".
            self._silence_sec += self._frame_sec
            if self._silence_sec >= self.hang_sec:
                self._stopped = True

    @property
    def speech_started(self):
        """True, sobald ein zusammenhaengender Sprachabschnitt min_speech_sec erreichte."""
        return self._speech_started

    @property
    def speech_run_sec(self):
        """Dauer des aktuellen zusammenhaengenden Sprachabschnitts in Sekunden."""
        return self._run_sec

    @property
    def stopped(self):
        """True, sobald nach Sprechbeginn die nachlaufende Stille hang_sec erreicht."""
        return self._stopped

    @property
    def silence_sec(self):
        """Aktuelle Dauer der nachlaufenden Stille in Sekunden."""
        return self._silence_sec


# ------------------------------------------------------------- Einstellungen
class Settings:
    """VAD-Einstellungen. Quelle: Attribute der Cfg (vad_autostop, ...), sonst
    die Abschnitte [vad] der config.ini.

        [vad]
        backend = auto            # auto | rms (rms schaltet Silero ab)
        silero_model =            # Pfad zu silero_vad.onnx; leer = Modellordner
        autostop = false          # Freihand-Modus nach Stille beenden
        autostop_silence = 1.5    # Sekunden Stille bis zum Auto-Stopp
    """

    def __init__(self, backend="auto", silero_model="", autostop=False,
                 autostop_silence=AUTOSTOP_SILENCE_DEFAULT):
        self.backend = backend
        self.silero_model = silero_model
        self.autostop = autostop
        self.autostop_silence = autostop_silence


def _read_ini():
    """config.ini als Parser; unlesbar oder kaputt: leerer Parser. Wirft nie."""
    p = configparser.ConfigParser(interpolation=None)
    try:
        from . import config
        p.read(config.CONFIG, encoding="utf-8")
    except Exception:    # noqa: BLE001 -- kaputte Datei: Vorgaben nehmen
        return configparser.ConfigParser(interpolation=None)
    return p


def _bool(value, default):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _float(value, default, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return min(max(v, lo), hi)


def settings(cfg=None):
    """Aktuelle VAD-Einstellungen. Eine Cfg mit den Attributen vad_backend,
    vad_silero_model, vad_autostop, vad_autostop_silence gewinnt; was ihr
    fehlt, kommt aus der config.ini."""
    ini = None

    def pick(attr, key, default):
        nonlocal ini
        value = getattr(cfg, attr, None) if cfg is not None else None
        if value is not None:
            return value
        if ini is None:
            ini = _read_ini()
        return ini.get("vad", key, fallback=default)

    backend = str(pick("vad_backend", "backend", "auto")).strip().lower()
    return Settings(
        backend=backend if backend in ("auto", "rms") else "auto",
        silero_model=str(pick("vad_silero_model", "silero_model", "")).strip(),
        autostop=_bool(pick("vad_autostop", "autostop", False), False),
        autostop_silence=_float(pick("vad_autostop_silence", "autostop_silence",
                                     AUTOSTOP_SILENCE_DEFAULT),
                                AUTOSTOP_SILENCE_DEFAULT, *AUTOSTOP_SILENCE_RANGE))


def make_classifier(cfg=None, log=None, silero_factory=None):
    """Klassifikator nach Einstellung: Silero, wenn onnxruntime UND das Modell da
    sind und ``backend`` es nicht abschaltet, sonst die adaptive Energieschwelle.
    Silero wird erst hier geladen -- wer VAD nicht braucht, importiert nichts."""
    s = settings(cfg)
    if s.backend != "rms":
        try:
            if silero_factory is None:
                from . import vad_silero
                silero_factory = vad_silero.create
            silero = silero_factory(s.silero_model, log=log)
        except Exception as exc:    # noqa: BLE001 -- Rueckfall auf RMS, nie ein Absturz
            silero = None
            if log:
                log("vad: Silero nicht nutzbar (%s) -> Energieschwelle" % type(exc).__name__)
        if silero is not None:
            return silero
    return RmsClassifier()
