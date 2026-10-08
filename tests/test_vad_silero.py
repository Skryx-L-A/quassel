"""Optionales Silero-Backend und Einstellungen: reine Logik gegen eine Attrappe
der ONNX-Sitzung. Es wird nie ein Modell geladen und nichts heruntergeladen."""
import os
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import config, vad, vad_silero
from quassel.vad import RmsClassifier, SilenceDetector

RATE = 16000
CHUNK = vad_silero.CHUNK


def pcm(amplitude, samples):
    return struct.pack(f"<{samples}h", *([amplitude] * samples))


class FakeSession:
    """Nachbildung der Silero-Sitzung: Wahrscheinlichkeit nach Lautstaerke des
    Rahmens (ohne Kontext), Zustand zaehlt die Aufrufe. Merkt sich alle Eingaben."""

    def __init__(self, probs=None, inputs=("input", "state", "sr"), outputs=2):
        self.calls = []
        self._probs = list(probs) if probs is not None else None
        self._inputs = [SimpleNamespace(name=n) for n in inputs]
        self._outputs = [SimpleNamespace(name="o%d" % i) for i in range(outputs)]

    def get_inputs(self):
        return self._inputs

    def get_outputs(self):
        return self._outputs

    def run(self, names, feed):
        self.calls.append({k: np.array(v, copy=True) for k, v in feed.items()})
        if self._probs is not None:
            p = self._probs.pop(0)
        else:
            frame = feed["input"][:, vad_silero.CONTEXT:]
            p = 0.9 if float(np.abs(frame).mean()) > 0.02 else 0.05
        return [np.array([[p]], dtype=np.float32), feed["state"] + 1.0]


def silero(probs=None, **kw):
    return vad_silero.SileroVad(FakeSession(probs, **kw), np)


# ------------------------------------------------------------ Streaming-Zustand
def test_frame_is_512_samples_with_64_context_samples():
    v = silero()
    assert v.frame_samples == 512
    v.is_speech(pcm(1000, 512))
    first = v._session.calls[0]
    assert first["input"].shape == (1, 64 + 512)
    assert first["state"].shape == (2, 1, 128)
    assert int(first["sr"]) == 16000
    assert not first["input"][:, :64].any(), "erster Rahmen: Kontext ist Stille"


def test_context_and_state_carry_over_between_chunks():
    v = silero()
    a = struct.pack("<512h", *range(512))
    b = struct.pack("<512h", *range(512, 1024))
    v.is_speech(a)
    v.is_speech(b)
    c1, c2 = v._session.calls
    expected = np.frombuffer(a, dtype="<i2").astype(np.float32)[-64:] / 32768.0
    assert np.allclose(c2["input"][0, :64], expected), "Kontext = letzte 64 Samples"
    assert float(c1["state"].max()) == 0.0 and float(c2["state"].max()) == 1.0


def test_reset_clears_state_context_and_hysteresis():
    v = silero([0.9, 0.9])
    v.is_speech(pcm(1000, 512))
    v.reset()
    v.is_speech(pcm(1000, 512))
    assert float(v._session.calls[1]["state"].max()) == 0.0
    assert not v._session.calls[1]["input"][:, :64].any()


def test_wrong_frame_size_is_refused():
    with pytest.raises(ValueError):
        silero().probability(pcm(0, 100))


def test_hysteresis_keeps_the_state_between_the_thresholds():
    v = silero([0.2, 0.9, 0.4, 0.3, 0.9, 0.34])
    got = [v.is_speech(pcm(0, 512)) for _ in range(6)]
    # 0.4 liegt zwischen 0.35 und 0.5: bleibt Sprache; 0.3 beendet sie.
    assert got == [False, True, True, False, True, False]


def test_silero_drives_the_silence_detector():
    d = SilenceDetector(rate=RATE, classifier=silero(), min_speech_sec=0.3,
                        hang_sec=1.0)
    d.feed(pcm(0, RATE // 2))
    assert not d.speech_started
    d.feed(pcm(3000, RATE // 2))
    assert d.speech_started
    d.feed(pcm(0, RATE * 2))
    assert d.stopped


# ----------------------------------------------------- Modell und Laufzeit finden
class FakeOrt:
    class SessionOptions:
        pass

    def __init__(self, session):
        self._session = session

    def InferenceSession(self, path, sess_options=None, providers=None):
        assert providers == ["CPUExecutionProvider"]
        return self._session


def test_no_model_means_no_silero(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELDIR", str(tmp_path))
    assert vad_silero.model_path() is None
    assert vad_silero.create() is None


def test_configured_path_wins_and_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELDIR", str(tmp_path))
    (tmp_path / "silero_vad.onnx").write_bytes(b"x")
    assert vad_silero.model_path("") == str(tmp_path / "silero_vad.onnx")
    assert vad_silero.model_path(str(tmp_path / "gibtsnicht.onnx")) is None


def test_model_without_onnxruntime_falls_back(tmp_path):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    logs = []
    assert vad_silero.create(str(model), log=logs.append, runtime=(np, None)) is None
    assert any("onnxruntime" in line for line in logs)


def test_incompatible_model_falls_back(tmp_path):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    bad = FakeOrt(FakeSession(inputs=("x",)))
    assert vad_silero.create(str(model), runtime=(np, bad)) is None


def test_model_and_runtime_give_a_classifier(tmp_path):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    vad_silero._sessions.clear()
    v = vad_silero.create(str(model), runtime=(np, FakeOrt(FakeSession())))
    assert isinstance(v, vad_silero.SileroVad)
    assert v.is_speech(pcm(3000, 512))
    # zweite Anlage nutzt dieselbe Sitzung (Modell nur einmal laden)
    again = vad_silero.create(str(model), runtime=(np, FakeOrt(FakeSession())))
    assert again._session is v._session


# ------------------------------------------------------------------ Einstellungen
def write_ini(tmp_path, monkeypatch, text):
    ini = tmp_path / "config.ini"
    ini.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG", str(ini))


def test_settings_defaults_without_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))
    s = vad.settings(SimpleNamespace())
    assert (s.backend, s.silero_model, s.autostop, s.autostop_silence) == \
        ("auto", "", False, 1.5)


def test_settings_read_from_ini(tmp_path, monkeypatch):
    write_ini(tmp_path, monkeypatch,
              "[vad]\nbackend = rms\nsilero_model = /x/s.onnx\nautostop = yes\n"
              "autostop_silence = 2.5\n")
    s = vad.settings(SimpleNamespace())
    assert (s.backend, s.silero_model, s.autostop, s.autostop_silence) == \
        ("rms", "/x/s.onnx", True, 2.5)


def test_settings_cfg_attributes_win_and_bad_values_fall_back(tmp_path, monkeypatch):
    write_ini(tmp_path, monkeypatch, "[vad]\nautostop = false\nautostop_silence = abc\n")
    s = vad.settings(SimpleNamespace(vad_autostop=True, vad_autostop_silence=99,
                                     vad_backend="weird"))
    assert s.autostop is True
    assert s.autostop_silence == 30.0           # begrenzt
    assert s.backend == "auto"                  # unbekannt -> auto
    s2 = vad.settings(SimpleNamespace())
    assert s2.autostop_silence == 1.5           # Muell -> Vorgabe


def test_settings_survive_a_broken_ini(tmp_path, monkeypatch):
    write_ini(tmp_path, monkeypatch, "[vad\nkaputt")
    assert vad.settings(SimpleNamespace()).autostop is False


# ---------------------------------------------------------- Rueckfall auf die Energieschwelle
def test_make_classifier_falls_back_to_rms(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))
    cfg = SimpleNamespace()
    assert isinstance(vad.make_classifier(cfg, silero_factory=lambda *a, **k: None),
                      RmsClassifier)

    def boom(*a, **k):
        raise RuntimeError("kaputtes Modell")

    logs = []
    assert isinstance(vad.make_classifier(cfg, log=logs.append, silero_factory=boom),
                      RmsClassifier)
    assert logs and "RuntimeError" in logs[0]


def test_make_classifier_uses_silero_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))
    sentinel = object()
    assert vad.make_classifier(SimpleNamespace(),
                               silero_factory=lambda *a, **k: sentinel) is sentinel


def test_backend_rms_never_touches_silero(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))

    def never(*a, **k):
        raise AssertionError("Silero darf nicht geladen werden")

    got = vad.make_classifier(SimpleNamespace(vad_backend="rms"), silero_factory=never)
    assert isinstance(got, RmsClassifier)


def test_importing_vad_does_not_import_onnxruntime():
    assert "onnxruntime" not in sys.modules
