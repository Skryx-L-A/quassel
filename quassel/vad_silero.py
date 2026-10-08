"""Optionales Silero-VAD-Backend (ONNX, MIT, rund 2 MB).

Quassel liefert das Modell NICHT mit und laedt es NICHT herunter. Es wird nur
benutzt, wenn beides vorhanden ist: das Python-Paket ``onnxruntime`` und die
Datei ``silero_vad.onnx`` (aus github.com/snakers4/silero-vad, Version 5 oder
6). Gesucht wird sie unter dem Pfad ``[vad] silero_model`` der config.ini, sonst
im Modellordner (config.MODELDIR). Fehlt eines von beiden, liefert create() None
und der Aufrufer bleibt bei der Energieschwelle (vad.RmsClassifier) -- gleiche
Schnittstelle: frame_samples, is_speech(frame), reset().

Silero rechnet bei 16 kHz in Rahmen zu genau 512 Samples (32 ms) und braucht
zwischen den Rahmen zwei Dinge: den LSTM-Zustand (2x1x128) und die letzten 64
Samples des Vorgaengerrahmens als Kontext. Beides haelt SileroVad je Instanz;
reset() beginnt eine neue Aeusserung.
"""
import os

RATE = 16000
CHUNK = 512            # Samples je Rahmen bei 16 kHz
CONTEXT = 64           # Kontext-Samples vor jedem Rahmen bei 16 kHz
STATE_SHAPE = (2, 1, 128)
MODEL_FILE = "silero_vad.onnx"
THRESHOLD = 0.5
NEG_MARGIN = 0.15      # Sprache endet erst unter THRESHOLD - NEG_MARGIN (Hysterese)

_sessions = {}         # (Pfad, mtime) -> onnxruntime-Sitzung, einmal je Modell


def model_path(configured=""):
    """Pfad der Modelldatei oder None, wenn keine da ist. Ein gesetzter Pfad gilt
    allein; ist er falsch, wird NICHT still im Modellordner weitergesucht."""
    if configured:
        path = os.path.expanduser(configured)
        return path if os.path.isfile(path) else None
    try:
        from . import config
        path = os.path.join(config.MODELDIR, MODEL_FILE)
    except Exception:    # noqa: BLE001
        return None
    return path if os.path.isfile(path) else None


def _import_runtime():
    try:
        import numpy
        import onnxruntime
    except ImportError:
        return None, None
    return numpy, onnxruntime


def _load_session(path, ort):
    """onnxruntime-Sitzung fuer path, je Modelldatei einmal geladen."""
    try:
        key = (path, os.stat(path).st_mtime)
    except OSError:
        return None
    if key not in _sessions:
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        _sessions[key] = ort.InferenceSession(
            path, sess_options=opts, providers=["CPUExecutionProvider"])
    return _sessions[key]


def _compatible(session):
    """Hat die Sitzung die Ein- und Ausgaenge von Silero v5/v6?"""
    names = {i.name for i in session.get_inputs()}
    return {"input", "state", "sr"} <= names and len(session.get_outputs()) >= 2


class SileroVad:
    """Silero-Klassifikator ueber einer (ggf. nachgebildeten) ONNX-Sitzung.

    ``session`` muss ``run(None, {"input", "state", "sr"})`` mit den Ausgaengen
    (Wahrscheinlichkeit, neuer Zustand) bieten; Tests reichen eine Attrappe herein.
    """

    frame_samples = CHUNK

    def __init__(self, session, np, threshold=THRESHOLD, neg_margin=NEG_MARGIN):
        self._session = session
        self._np = np
        self.threshold = threshold
        self.neg_threshold = max(0.01, threshold - neg_margin)
        self._sr = np.array(RATE, dtype=np.int64)
        self.reset()

    def reset(self):
        np = self._np
        self._state = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)
        self._speaking = False

    def probability(self, frame):
        """Sprachwahrscheinlichkeit (0..1) eines Rahmens aus genau 512 s16le-Samples."""
        np = self._np
        if len(frame) != CHUNK * 2:
            raise ValueError("Silero braucht Rahmen zu %d Samples" % CHUNK)
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
        x = np.concatenate([self._context, samples.reshape(1, CHUNK)], axis=1)
        out, state = self._session.run(
            None, {"input": x, "state": self._state, "sr": self._sr})[:2]
        self._state = state
        self._context = x[:, -CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])

    def is_speech(self, frame):
        """Sprache mit Hysterese: ein Rahmen zwischen den beiden Schwellen behaelt
        den bisherigen Zustand, damit die Entscheidung bei leisen Silben nicht flattert."""
        p = self.probability(frame)
        if p >= self.threshold:
            self._speaking = True
        elif p < self.neg_threshold:
            self._speaking = False
        return self._speaking


def create(configured="", log=None, runtime=None):
    """SileroVad oder None. None heisst: onnxruntime oder Modell fehlen (oder das
    Modell passt nicht) -- der Aufrufer faellt dann auf die Energieschwelle
    zurueck. ``runtime`` ist ein (numpy, onnxruntime)-Paar fuer Tests."""
    path = model_path(configured)
    if path is None:
        return None
    np, ort = runtime if runtime is not None else _import_runtime()
    if ort is None:
        if log:
            log("vad: Silero-Modell gefunden, aber onnxruntime fehlt -> Energieschwelle")
        return None
    session = _load_session(path, ort)
    if session is None or not _compatible(session):
        if log:
            log("vad: Silero-Modell passt nicht (Ein-/Ausgaenge) -> Energieschwelle")
        return None
    return SileroVad(session, np)
