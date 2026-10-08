"""Wake-Word: Aufnahme einer Aeusserung mit vorgeschaltetem Keyword-Spotter,
openWakeWord-Pipeline und Laden der Modelle -- alles gegen Attrappen. Kein
Mikrofon, kein Modell, kein Netz, keine Threads."""
import os
import struct
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import config, vad, wakeword

RATE = 16000
CHUNK = wakeword.SPOTTER_CHUNK


def pcm(amplitude, seconds):
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *([amplitude] * n))


class FakeRec:
    """Recorder-Attrappe: pro sleep() des Aufrufs kommen poll Sekunden Ton dazu."""

    def __init__(self, audio, poll=0.1, start_ok=True):
        self._audio = audio
        self._poll = poll
        self._start_ok = start_ok
        self.data = b""
        self.started = self.stopped = False

    def start(self, mic):
        self.started = self._start_ok
        return self._start_ok

    def raw_bytes(self):
        return self.data

    def stop(self):
        self.stopped = True

    def tick(self):
        n = int(RATE * self._poll) * 2
        self.data += self._audio[len(self.data):len(self.data) + n]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = 0

    def __call__(self):
        return self.now

    def sleep(self, dt):
        self.now += dt
        self.sleeps += 1


def run(audio, spotter=None, classifier=None, **kw):
    rec, clock = FakeRec(audio), Clock()

    def sleep(dt):
        clock.sleep(dt)
        rec.tick()

    logs = []
    out = wakeword.capture_utterance(
        rec, "default", classifier or vad.RmsClassifier(threshold=350.0),
        spotter=spotter, log=logs.append, sleep=sleep, clock=clock, **kw)
    return out, rec, clock, logs


class LoudSpotter:
    """Schlaegt an, sobald `needed` laute Schritte (Phrase) gehoert wurden."""
    chunk_samples = CHUNK

    def __init__(self, needed=8):
        self.needed, self.heard, self.resets, self.fed = needed, 0, 0, 0

    def reset(self):
        self.resets += 1
        self.heard = 0

    def feed(self, chunk):
        assert len(chunk) == CHUNK * 2
        self.fed += 1
        samples = struct.unpack("<%dh" % CHUNK, chunk)
        self.heard = self.heard + 1 if abs(samples[0]) > 4000 else 0
        return self.heard >= self.needed


# --------------------------------------------------------- ohne Spotter (bisher)
def test_without_spotter_it_records_until_the_pause():
    audio = pcm(0, 0.5) + pcm(5000, 1.0) + pcm(0, 3.0)
    out, rec, clock, logs = run(audio)
    assert out is not None and len(out) > 16000
    assert rec.stopped
    assert clock.now < 3.5, "nach der Stille ist Schluss, nicht erst nach 12 s"


def test_without_spotter_noise_only_gives_nothing():
    out, rec, _, logs = run(pcm(0, 4.0), window=3.0)
    assert out is None and rec.stopped


def test_without_spotter_the_room_noise_is_not_speech():
    out, *_ = run(pcm(600, 4.0), classifier=vad.RmsClassifier(), window=3.0)
    assert out is None


def test_microphone_failure_gives_nothing():
    rec = FakeRec(b"", start_ok=False)
    assert wakeword.capture_utterance(rec, "default", vad.RmsClassifier()) is None
    assert not rec.stopped


def test_active_flag_ends_the_loop_and_the_recorder_is_stopped():
    rec, clock = FakeRec(pcm(5000, 5.0)), Clock()
    states = iter([True, True, False])
    out = wakeword.capture_utterance(
        rec, "default", vad.RmsClassifier(threshold=350.0),
        active=lambda: next(states), sleep=lambda dt: (clock.sleep(dt), rec.tick()),
        clock=clock)
    assert rec.stopped and clock.sleeps == 2
    assert out is None or len(out) < 16000 * 2


def test_recorder_is_stopped_even_when_the_classifier_raises():
    class Boom:
        frame_samples = 480

        def is_speech(self, frame):
            raise RuntimeError("kaputt")

    rec, clock = FakeRec(pcm(5000, 1.0)), Clock()
    try:
        wakeword.capture_utterance(rec, "default", Boom(),
                                   sleep=lambda dt: (clock.sleep(dt), rec.tick()),
                                   clock=clock)
    except RuntimeError:
        pass
    assert rec.stopped


# ----------------------------------------------------------------- mit Spotter
def test_spotter_gates_everything_before_the_keyword():
    """Normales Gespraech im Raum: der Spotter schlaegt nie an -> kein Ergebnis,
    also keine Whisper-Anfrage."""
    talk = pcm(3000, 0.4) + pcm(0, 0.3)
    out, rec, clock, _ = run(talk * 10, spotter=LoudSpotter(needed=99), window=6.0)
    assert out is None and rec.stopped


def test_spotter_hit_returns_preroll_plus_the_sentence_up_to_the_pause():
    spotter = LoudSpotter(needed=8)
    audio = (pcm(0, 3.0) + pcm(6000, 0.64)          # Phrase (8 Schritte)
             + pcm(3000, 1.2) + pcm(0, 3.0))         # Satz, dann Stille
    out, rec, clock, logs = run(audio, spotter=spotter, window=30.0)
    assert out is not None
    # Anfang: hoechstens 2 s Vorlauf VOR dem Ende der Phrase (Phrase + Satz im Ergebnis).
    phrase_end = int(RATE * 3.64) * 2
    assert len(out) > 0 and rec.data.endswith(out[-1000:])
    start = len(rec.data) - len(out)
    assert phrase_end - start <= int(2.0 * RATE) * 2 + CHUNK * 2
    assert b"\x70\x17" in out[:int(RATE * 2.2) * 2], "die Phrase (6000) steckt im Vorlauf"
    assert spotter.resets == 1
    assert any("fired=True" in line for line in logs)


def test_spotter_hit_in_the_first_seconds_starts_at_zero():
    audio = pcm(6000, 0.64) + pcm(3000, 0.8) + pcm(0, 3.0)
    out, rec, *_ = run(audio, spotter=LoudSpotter(needed=8), window=30.0)
    assert out is not None and out.startswith(pcm(6000, 0.1))


def test_logs_never_contain_audio_content():
    audio = pcm(6000, 0.64) + pcm(3000, 0.8) + pcm(0, 3.0)
    _, _, _, logs = run(audio, spotter=LoudSpotter(needed=8), window=30.0)
    assert all(set(line) <= set("abcdefghijklmnopqrstuvwxyz0123456789 :,=_") for line in (l.lower() for l in logs))


# ------------------------------------------------------ openWakeWord-Pipeline
class FakeSession:
    def __init__(self, name, shape, scores=None, calls=None):
        self._in = SimpleNamespace(name=name)
        self.shape, self.scores, self.calls = shape, scores, calls
        self.inputs = []

    def get_inputs(self):
        return [self._in]

    def run(self, names, feed):
        x = feed[self._in.name]
        self.inputs.append(x.copy())
        if self.scores is not None:
            return [np.full((1, 1), self.scores.pop(0) if self.scores else 0.0,
                            dtype=np.float32)]
        return [np.ones(self.shape, dtype=np.float32) * 10.0]


def make_spotter(scores, threshold=0.5, refractory=3):
    mel = FakeSession("input", (1, 1, 8, 32))
    emb = FakeSession("input", (1, 1, 1, 96))
    model = FakeSession("input", None, scores=list(scores))
    sp = wakeword.OpenWakeWordSpotter(np, mel, emb, model, threshold=threshold,
                                      refractory_chunks=refractory)
    return sp, mel, emb, model


def chunk(value=1000):
    return pcm(value, CHUNK / RATE)


def test_pipeline_needs_76_mel_rows_and_16_embeddings_before_it_decides():
    sp, mel, emb, model = make_spotter([0.9] * 50)
    results = [sp.feed(chunk()) for _ in range(24)]
    assert not any(results) and not model.inputs
    assert sp.feed(chunk()) is True                 # 25. Schritt: erste Entscheidung
    assert model.inputs[0].shape == (1, 16, 96)
    assert emb.inputs[0].shape == (1, 76, 32, 1)
    assert mel.inputs[0].shape == (1, CHUNK + 480)
    assert np.allclose(emb.inputs[0], 10.0 / 10.0 + 2.0), "Mel-Skalierung x/10+2"


def test_pipeline_threshold_and_refractory_period():
    # Das Schluesselwort-Modell laeuft erst ab dem 25. Schritt; ab da je ein Wert.
    sp, *_ = make_spotter([0.2, 0.9, 0.95, 0.99, 0.3, 0.9, 0.9, 0.4], refractory=3)
    fired = [sp.feed(chunk()) for _ in range(32)]
    # Schritt 26 (0.9) schlaegt an; die naechsten drei sind gesperrt; Schritt 30 (0.9) wieder.
    assert [i for i, f in enumerate(fired) if f] == [25, 29]
    assert abs(sp.last_score - 0.4) < 1e-6


def test_pipeline_reset_starts_over():
    sp, mel, emb, model = make_spotter([0.9] * 80)
    for _ in range(25):
        sp.feed(chunk())
    sp.reset()
    assert sp.last_score == 0.0
    assert not any(sp.feed(chunk()) for _ in range(24))


def test_pipeline_rejects_wrong_chunk_size():
    sp, *_ = make_spotter([])
    try:
        sp.feed(b"\x00" * 100)
    except ValueError:
        return
    raise AssertionError("falsche Schrittgroesse haette abgelehnt werden muessen")


# ---------------------------------------------------------------- Modelle laden
class FakeOrt:
    class SessionOptions:
        pass

    def InferenceSession(self, path, sess_options=None, providers=None):
        return FakeSession("input", (1, 1, 8, 32))


def spotter_dir(tmp_path, with_helpers=True):
    d = tmp_path / "models"
    d.mkdir()
    (d / "hey_quassel.onnx").write_bytes(b"m")
    if with_helpers:
        (d / wakeword.MEL_FILE).write_bytes(b"m")
        (d / wakeword.EMBED_FILE).write_bytes(b"e")
    return d


def cfg_for(path, threshold=0.7):
    return SimpleNamespace(wakeword_spotter_model=str(path),
                           wakeword_spotter_threshold=threshold)


def test_no_spotter_configured_means_todays_behaviour(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))
    assert wakeword.load_spotter(SimpleNamespace()) is None


def test_spotter_loads_with_model_helpers_and_runtime(tmp_path):
    d = spotter_dir(tmp_path)
    sp = wakeword.load_spotter(cfg_for(d / "hey_quassel.onnx"), runtime=(np, FakeOrt()))
    assert isinstance(sp, wakeword.OpenWakeWordSpotter) and sp.threshold == 0.7


def test_missing_model_runtime_or_helpers_fall_back_with_a_log_line(tmp_path):
    logs = []
    d = spotter_dir(tmp_path, with_helpers=False)
    path = d / "hey_quassel.onnx"
    assert wakeword.load_spotter(cfg_for(d / "gibts.onnx"), logs.append,
                                 runtime=(np, FakeOrt())) is None
    assert wakeword.load_spotter(cfg_for(path), logs.append, runtime=(np, None)) is None
    assert wakeword.load_spotter(cfg_for(path), logs.append, runtime=(np, FakeOrt())) is None
    assert len(logs) == 3 and all(line.startswith("wake: ") for line in logs)


def test_broken_model_falls_back(tmp_path):
    class BrokenOrt(FakeOrt):
        def InferenceSession(self, *a, **k):
            raise RuntimeError("kein ONNX")

    d = spotter_dir(tmp_path)
    logs = []
    assert wakeword.load_spotter(cfg_for(d / "hey_quassel.onnx"), logs.append,
                                 runtime=(np, BrokenOrt())) is None
    assert "RuntimeError" in logs[0]


def test_wake_settings_from_ini_and_bounds(tmp_path, monkeypatch):
    ini = tmp_path / "config.ini"
    ini.write_text("[wakeword]\nspotter_model = /x/y.onnx\nspotter_threshold = 5\n"
                   "verify = no\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG", str(ini))
    s = wakeword.wake_settings(SimpleNamespace())
    assert (s.spotter_model, s.spotter_threshold, s.verify) == ("/x/y.onnx", 0.99, False)
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))
    d = wakeword.wake_settings(SimpleNamespace())
    assert (d.spotter_model, d.spotter_threshold, d.verify) == ("", 0.5, True)
