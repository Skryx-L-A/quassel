"""Offline-Tests für die Variante über die transcribe_cpp-Python-Bindung.

Die Bindung wird nie importiert: eine Attrappe ersetzt Model/Session.
"""
import os
import sys
import time
import wave
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel.backends import SpeechCapabilities, TranscriptionOptions
from quassel.backends import tcpp_families as fams
from quassel.backends import transcribe_cpp_py as tpy
from quassel.backends.transcribe_cpp_py import TranscribeCppPythonBackend


def opts(lang="de", **kw):
    return TranscriptionOptions(SimpleNamespace(language=lang), timeout=5,
                                language_hint=lang, **kw)


def write_wav(path, seconds=0.1, rate=16000, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(seconds * rate) * channels)
    return str(path)


Seg = SimpleNamespace


class FakeError(Exception):
    def __init__(self, status, partial=None):
        super().__init__(f"status {status}")
        self.status = status
        self.partial_result = partial


class FakeBinding:
    def __init__(self, result=None, exc=None, delay=0.0):
        self.result, self.exc, self.delay = result, exc, delay
        self.loads, self.runs, self.cancelled = [], [], 0
        self.closed = 0
        binding = self

        class Session:
            def run(self, pcm, **kwargs):
                binding.runs.append((len(pcm), kwargs))
                if binding.delay:
                    deadline = time.monotonic() + binding.delay
                    while time.monotonic() < deadline and not binding.cancelled:
                        time.sleep(0.005)
                    if binding.cancelled:
                        raise FakeError(13)
                if binding.exc:
                    raise binding.exc
                return binding.result

            def cancel(self):
                binding.cancelled += 1

        class Model:
            def __init__(self, path, backend="auto"):
                binding.loads.append((path, backend))

            def session(self, n_threads=0):
                binding.threads = n_threads
                return Session()

            def close(self):
                binding.closed += 1

        self.Model = Model


def result(text=" Hallo Welt", segments=(), words=()):
    return SimpleNamespace(text=text, segments=segments, words=words)


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "qwen3-asr-1.7b-Q8_0.gguf"
    path.write_bytes(b"GGUF")
    return path


def test_without_binding_the_backend_is_unhealthy(model, monkeypatch):
    monkeypatch.setattr(tpy, "load_binding", lambda: None)
    be = TranscribeCppPythonBackend(model)
    assert be.health() is False and be.warmup() is False
    assert be.transcribe("x.wav", opts()).error == fams.ERR_UNAVAILABLE


def test_load_binding_returns_none_when_the_import_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "transcribe_cpp", None)   # ImportError
    assert tpy.load_binding() is None


def test_model_is_loaded_once_and_kept(model, tmp_path):
    fake = FakeBinding(result=result())
    be = TranscribeCppPythonBackend(model, compute="metal", threads=4,
                                    binding=fake)
    assert be.health() is True and be.warmup() is True
    wav = write_wav(tmp_path / "a.wav")
    assert be.transcribe(wav, opts()).text == "Hallo Welt"
    assert be.transcribe(wav, opts()).text == "Hallo Welt"
    assert fake.loads == [(str(model), "metal")] and fake.threads == 4
    be.close()
    assert fake.closed == 1


def test_dictionary_and_context_go_in_process_for_qwen(model, tmp_path):
    fake = FakeBinding(result=result())
    be = TranscribeCppPythonBackend(model, binding=fake,
                                    vocabulary=["Quassel", "Skryx"])
    assert be.capabilities == SpeechCapabilities(True, True, False, False)
    be.transcribe(write_wav(tmp_path / "a.wav"), opts("mixed", prompt_extra="Kontext"))
    _, kwargs = fake.runs[0]
    assert kwargs == {"language": None, "timestamps": "auto",
                      "vocabulary": ["Quassel", "Skryx"], "prompt": "Kontext"}


def test_cohere_gets_language_fallback_and_no_prompt(tmp_path):
    path = tmp_path / "cohere-transcribe-03-2026-Q5_K_M.gguf"
    path.write_bytes(b"GGUF")
    fake = FakeBinding(result=result())
    be = TranscribeCppPythonBackend(path, binding=fake, vocabulary=["Quassel"])
    res = be.transcribe(write_wav(tmp_path / "a.wav"),
                        opts("mixed", prompt_extra="Kontext"))
    assert fake.runs[0][1] == {"language": "de", "timestamps": "auto"}
    assert res.language == "de" and res.notes
    assert be.quant == "Q5_K_M"


def test_word_and_segment_times_are_returned_in_seconds(model, tmp_path):
    segs = (Seg(text=" Hallo Welt", t0_ms=0, t1_ms=1200),)
    words = (Seg(text=" Hallo", t0_ms=0, t1_ms=500),
             Seg(text=" Welt", t0_ms=600, t1_ms=1200))
    fake = FakeBinding(result=result(segments=segs, words=words))
    res = TranscribeCppPythonBackend(model, binding=fake).transcribe(
        write_wav(tmp_path / "a.wav"), opts())
    assert res.segments == ({"start": 0.0, "end": 1.2, "text": "Hallo Welt"},)
    assert res.words == ({"start": 0.0, "end": 0.5, "text": "Hallo"},
                         {"start": 0.6, "end": 1.2, "text": "Welt"})


def test_dummy_segment_without_timing_is_dropped(model, tmp_path):
    fake = FakeBinding(result=result(segments=(Seg(text="x", t0_ms=0, t1_ms=0),)))
    res = TranscribeCppPythonBackend(model, binding=fake).transcribe(
        write_wav(tmp_path / "a.wav"), opts())
    assert res.segments == ()


@pytest.mark.parametrize("status,error", [
    (10, fams.ERR_LANGUAGE), (17, fams.ERR_TOO_LONG), (7, fams.ERR_OOM),
    (8, fams.ERR_BACKEND), (99, fams.ERR_BACKEND)])
def test_status_errors_are_classified(model, tmp_path, status, error):
    fake = FakeBinding(exc=FakeError(status))
    res = TranscribeCppPythonBackend(model, binding=fake).transcribe(
        write_wav(tmp_path / "a.wav"), opts())
    assert res.text is None and res.error == error


def test_truncated_output_keeps_the_partial_text(model, tmp_path):
    fake = FakeBinding(exc=FakeError(18, partial=result(text=" Teil")))
    res = TranscribeCppPythonBackend(model, binding=fake).transcribe(
        write_wav(tmp_path / "a.wav"), opts())
    assert res.text == "Teil" and res.error == fams.ERR_TRUNCATED


def test_deadline_cancels_the_run(model, tmp_path):
    fake = FakeBinding(result=result(), delay=2.0)
    be = TranscribeCppPythonBackend(model, binding=fake)
    o = TranscriptionOptions(SimpleNamespace(language="de"), timeout=0.05,
                             language_hint="de")
    start = time.monotonic()
    res = be.transcribe(write_wav(tmp_path / "a.wav"), o)
    assert res.error == fams.ERR_TIMEOUT and fake.cancelled == 1
    assert time.monotonic() - start < 1.5


def test_model_load_failure_is_reported(model, tmp_path):
    fake = FakeBinding(result=result())

    def broken(path, backend="auto"):
        raise FakeError(4)
    fake.Model = broken
    res = TranscribeCppPythonBackend(model, binding=fake).transcribe(
        write_wav(tmp_path / "a.wav"), opts())
    assert res.error == fams.ERR_MODEL_LOAD


def test_wrong_audio_format_is_not_resampled(model, tmp_path):
    fake = FakeBinding(result=result())
    be = TranscribeCppPythonBackend(model, binding=fake)
    assert be.transcribe(write_wav(tmp_path / "a.wav", rate=44100),
                         opts()).error == fams.ERR_AUDIO
    assert be.transcribe(write_wav(tmp_path / "b.wav", channels=2),
                         opts()).error == fams.ERR_AUDIO
    assert be.transcribe(b"raw", opts()).error == fams.ERR_BAD_INPUT
    assert fake.runs == []


def test_pcm_conversion_is_exact():
    import array
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(array.array("h", [0, 16384, -32768]).tobytes()
                          if sys.byteorder == "little" else b"")
        if sys.byteorder == "little":
            assert list(tpy.read_wav_float(path)) == [0.0, 0.5, -1.0]
