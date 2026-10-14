"""Offline-Tests für den transcribe.cpp-CLI-Adapter (Prozess als Attrappe)."""
import json
import os
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel.backends import (SpeechCapabilities, SpeechResult,
                              TranscriptionOptions, TranscribeCppBackend)
from quassel.backends import tcpp_families as fams


OPTS = TranscriptionOptions(SimpleNamespace(language="de"), timeout=7,
                            language_hint="de")
HEADER = '{"type":"batch_header","load_ms":812.0}'


def opts(lang, **kw):
    return TranscriptionOptions(SimpleNamespace(language=lang), timeout=7,
                                language_hint=lang, **kw)


def jsonl(**record):
    record.setdefault("file", "/x/clip.wav")
    record.setdefault("mel_ms", 1.0)
    record.setdefault("encode_ms", 2.0)
    record.setdefault("decode_ms", 3.0)
    return HEADER + "\n" + json.dumps(record, ensure_ascii=False) + "\n"


class FakeRun:
    """Attrappe für subprocess.run; hält argv und den Inhalt der Temp-Dateien."""

    def __init__(self, stdout="", returncode=0, stderr="", exc=None):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.exc = exc
        self.calls = []
        self.files = {}

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        for flag in ("--batch", "--vocabulary-file"):
            if flag in argv:
                path = argv[argv.index(flag) + 1]
                self.files[flag] = (path, Path(path).read_text(encoding="utf-8"),
                                    oct(os.stat(path).st_mode & 0o777))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode,
                                           stdout=self.stdout, stderr=self.stderr)


def backend(tmp_path, family="cohere", **kw):
    model = tmp_path / "model-Q8_0.gguf"
    model.write_bytes(b"GGUF")
    return TranscribeCppBackend(binary="/opt/transcribe-cli", model=model,
                                family=family, **kw)


def write_wav(path, seconds, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))
    return path


# --- Vertrag ohne Konfiguration (früherer Stub bleibt kompatibel) ---------

def test_without_family_no_capabilities_are_claimed():
    assert TranscribeCppBackend().capabilities == SpeechCapabilities(
        prompt=False, language_hint=False, streaming=False, timestamps=False)


def test_without_binary_path_the_backend_is_skipped():
    stub = TranscribeCppBackend()
    assert stub.health() is False
    result = stub.transcribe("clip.wav", OPTS)
    assert result.text is None and result.error == fams.ERR_NO_BINARY


def test_health_requires_executable_binary_model_and_family(tmp_path):
    binary = tmp_path / "transcribe-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    model = tmp_path / "cohere-transcribe-03-2026-Q8_0.gguf"
    assert TranscribeCppBackend(binary=binary, model=model).health() is False
    model.write_bytes(b"GGUF")
    assert TranscribeCppBackend(binary=binary, model=model).health() is True
    other = tmp_path / "unbenannt.gguf"
    other.write_bytes(b"GGUF")
    assert TranscribeCppBackend(binary=binary, model=other).health() is False
    assert TranscribeCppBackend(binary=binary, model=other,
                                family="parakeet").health() is True
    if os.name != "nt":   # Windows kennt kein Exec-Bit: os.access(X_OK) ist dort immer wahr
        binary.chmod(0o644)   # nicht ausführbar
        assert TranscribeCppBackend(binary=binary, model=model).health() is False


def test_warmup_is_an_explicit_no_op(tmp_path):
    assert backend(tmp_path).warmup() is False


def test_unknown_family_is_a_caller_error(tmp_path):
    with pytest.raises(ValueError):
        backend(tmp_path, family="erfunden")


# --- CLI-Syntax ------------------------------------------------------------

def test_argv_uses_the_upstream_batch_jsonl_contract(tmp_path, monkeypatch):
    fake = FakeRun(stdout=jsonl(text=" Hallo Welt"))
    monkeypatch.setattr(subprocess, "run", fake)
    be = backend(tmp_path, compute="metal", threads=6, device=0)
    result = be.transcribe(str(tmp_path / "clip.wav"), OPTS)
    assert result == SpeechResult(text="Hallo Welt", language="de")
    argv, kwargs = fake.calls[0]
    list_path = fake.files["--batch"][0]
    assert argv == ["/opt/transcribe-cli", "-m", str(tmp_path / "model-Q8_0.gguf"),
                    "--batch", list_path, "--batch-jsonl", "-q",
                    "--backend", "metal", "-l", "de", "--threads", "6",
                    "--device", "0"]
    assert fake.files["--batch"][1] == str(tmp_path / "clip.wav") + "\n"
    assert kwargs["timeout"] == 7 and kwargs["check"] is False
    assert not os.path.exists(list_path)       # Temp-Dateien aufgeräumt


def test_invalid_compute_backend_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        backend(tmp_path, compute="opencl")


def test_raw_pcm_is_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(exc=AssertionError("nie")))
    result = backend(tmp_path).transcribe(b"raw pcm", TranscriptionOptions(None))
    assert result.text is None and result.error == fams.ERR_BAD_INPUT


def test_a_newline_in_the_path_cannot_inject_a_second_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(exc=AssertionError("nie")))
    result = backend(tmp_path).transcribe("a.wav\n/etc/passwd", OPTS)
    assert result.error == fams.ERR_BAD_INPUT


# --- Sprache je Familie ----------------------------------------------------

@pytest.mark.parametrize("family,lang,flag,noted", [
    ("cohere", "de", "de", False),
    ("cohere", "en", "en", False),
    ("cohere", "mixed", "de", True),
    ("cohere", "auto", "de", True),
    ("canary", "mixed", "de", True),
    ("parakeet", "de", "de", False),
    ("parakeet", "mixed", None, False),
    ("whisper", "auto", None, False),
    ("qwen3-asr", "mixed", None, False),
    ("nemotron", "de", "de-DE", False),
    ("nemotron", "en", "en-US", False),
    ("nemotron", "auto", "auto", False),
    ("nemotron", "mixed", "de-DE", True),
    ("voxtral-realtime", "de", None, True),
])
def test_language_per_family(tmp_path, monkeypatch, family, lang, flag, noted):
    fake = FakeRun(stdout=jsonl(text="x"))
    monkeypatch.setattr(subprocess, "run", fake)
    result = backend(tmp_path, family=family).transcribe("clip.wav", opts(lang))
    argv = fake.calls[0][0]
    if flag is None:
        assert "-l" not in argv
    else:
        assert argv[argv.index("-l") + 1] == flag
    assert bool(result.notes) is noted
    assert result.language == flag


def test_language_falls_back_to_cfg_when_no_hint(tmp_path, monkeypatch):
    fake = FakeRun(stdout=jsonl(text="x"))
    monkeypatch.setattr(subprocess, "run", fake)
    o = TranscriptionOptions(SimpleNamespace(language="en"), timeout=7)
    backend(tmp_path).transcribe("clip.wav", o)
    argv = fake.calls[0][0]
    assert argv[argv.index("-l") + 1] == "en"


# --- Fähigkeiten je Familie ------------------------------------------------

@pytest.mark.parametrize("family,caps", [
    ("whisper", (True, True, False, True)),
    ("qwen3-asr", (True, True, False, False)),
    ("cohere", (False, True, False, False)),
    ("parakeet", (False, True, False, True)),
    ("canary", (False, True, False, False)),
    ("nemotron", (False, True, True, True)),
    ("voxtral-realtime", (False, False, True, False)),
])
def test_capabilities_per_family(tmp_path, family, caps):
    assert backend(tmp_path, family=family).capabilities == SpeechCapabilities(*caps)


def test_family_is_guessed_from_handy_computer_file_names(tmp_path):
    for name, fam in (("whisper-large-v3-turbo-Q8_0.gguf", "whisper"),
                      ("qwen3-asr-1.7b-Q5_K_M.gguf", "qwen3-asr"),
                      ("cohere-transcribe-03-2026-Q8_0.gguf", "cohere"),
                      ("canary-1b-v2-Q8_0.gguf", "canary"),
                      ("parakeet-tdt-0.6b-v3-Q8_0.gguf", "parakeet")):
        be = TranscribeCppBackend(binary="/b", model=tmp_path / name)
        assert be.family.name == fam


def test_quantization_is_read_from_the_file_name():
    assert fams.quant_from_path("/m/cohere-transcribe-03-2026-Q5_K_M.gguf") == "Q5_K_M"
    assert fams.quant_from_path("C:\\m\\qwen3-asr-1.7b-Q8_0.gguf") == "Q8_0"
    assert fams.quant_from_path("/m/model.gguf") is None


# --- Wörterbuch und Kontext ------------------------------------------------

def test_dictionary_goes_through_a_private_file_for_vocabulary_families(
        tmp_path, monkeypatch):
    fake = FakeRun(stdout=jsonl(text="Quassel"))
    monkeypatch.setattr(subprocess, "run", fake)
    be = backend(tmp_path, family="qwen3-asr",
                 vocabulary=lambda: ["Quassel", " Skryx\n", ""])
    be.transcribe("clip.wav", opts("de", prompt_extra="schon diktierter Satz"))
    argv = fake.calls[0][0]
    path, content, mode = fake.files["--vocabulary-file"]
    assert content == "Quassel\nSkryx\n"
    if os.name != "nt":
        assert mode == "0o600"
    # Kontexttext nie in der Befehlszeile (ps-sichtbar).
    assert "--prompt" not in argv
    assert not any("diktierter" in a for a in argv)
    assert not os.path.exists(path)


def test_no_dictionary_file_for_families_without_vocabulary(tmp_path, monkeypatch):
    fake = FakeRun(stdout=jsonl(text="x"))
    monkeypatch.setattr(subprocess, "run", fake)
    backend(tmp_path, family="cohere", vocabulary=["Quassel"]).transcribe(
        "clip.wav", OPTS)
    assert "--vocabulary-file" not in fake.calls[0][0]


# --- Ergebnis, Zeiten, Fehlerklassen ---------------------------------------

def test_segments_are_converted_to_seconds(tmp_path, monkeypatch):
    rows = [{"t0_ms": 0, "t1_ms": 1500, "text": " Hallo"},
            {"t0_ms": 1500, "t1_ms": 900, "text": "kaputt"},
            {"t0_ms": 1500, "t1_ms": 2600, "text": " Welt"}]
    monkeypatch.setattr(subprocess, "run",
                        FakeRun(stdout=jsonl(text="Hallo Welt", segments=rows)))
    result = backend(tmp_path, family="parakeet").transcribe("clip.wav", OPTS)
    assert result.segments == ({"start": 0.0, "end": 1.5, "text": "Hallo"},
                               {"start": 1.5, "end": 2.6, "text": "Welt"})


@pytest.mark.parametrize("status,error,keeps_text", [
    ("output truncated: decode hit the context/generation cap before end-of-stream",
     fams.ERR_TRUNCATED, True),
    ("output repetition: decode stopped when the output began repeating itself",
     fams.ERR_REPETITION, True),
    ("unsupported language", fams.ERR_LANGUAGE, False),
    ("input audio too long for model context", fams.ERR_TOO_LONG, False),
    ("out of memory", fams.ERR_OOM, False),
    ("backend error", fams.ERR_BACKEND, False),
    ("wav: not a RIFF file", fams.ERR_AUDIO, False),
    ("etwas Neues", fams.ERR_EXIT, False),
])
def test_record_error_field_maps_to_error_classes(tmp_path, monkeypatch,
                                                  status, error, keeps_text):
    monkeypatch.setattr(subprocess, "run", FakeRun(
        stdout=jsonl(text="Teil" if keeps_text else "", error=status),
        returncode=1))
    result = backend(tmp_path).transcribe("clip.wav", OPTS)
    assert result.error == error
    assert result.text == ("Teil" if keeps_text else None)


def test_model_load_failure_on_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(
        stdout="", returncode=1, stderr="model load: gguf load error\n"))
    result = backend(tmp_path).transcribe("clip.wav", OPTS)
    assert result.text is None and result.error == fams.ERR_MODEL_LOAD


def test_garbage_output_with_exit_zero_is_bad_output(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(stdout="text: Hallo\n"))
    result = backend(tmp_path).transcribe("clip.wav", OPTS)
    assert result.text is None and result.error == fams.ERR_OUTPUT


def test_timeout_and_spawn_failure(tmp_path, monkeypatch):
    be = backend(tmp_path)
    monkeypatch.setattr(subprocess, "run",
                        FakeRun(exc=subprocess.TimeoutExpired(["x"], 7)))
    assert be.transcribe("clip.wav", OPTS).error == fams.ERR_TIMEOUT
    monkeypatch.setattr(subprocess, "run", FakeRun(exc=FileNotFoundError()))
    assert be.transcribe("clip.wav", OPTS).error == fams.ERR_SPAWN


def test_cohere_rejects_clips_over_400_seconds_before_spawning(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(exc=AssertionError("nie")))
    wav = write_wav(tmp_path / "lang.wav", 401, rate=1000)
    result = backend(tmp_path).transcribe(str(wav), OPTS)
    assert result.error == fams.ERR_TOO_LONG


def test_missing_model_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(exc=AssertionError("nie")))
    be = TranscribeCppBackend(binary="/opt/transcribe-cli")
    assert be.transcribe("clip.wav", OPTS).error == fams.ERR_NO_MODEL


def test_import_via_backends_package():
    import quassel.backends
    assert quassel.backends.TranscribeCppBackend is TranscribeCppBackend
    assert Path(quassel.backends.__file__).is_file()
