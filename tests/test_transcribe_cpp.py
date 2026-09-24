"""Offline-Tests für den transcribe.cpp-CLI-Stub."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel.backends import (SpeechCapabilities, SpeechResult,
                              TranscriptionOptions, TranscribeCppBackend)


OPTS = TranscriptionOptions(SimpleNamespace(language="de"), timeout=7)


def test_stub_declares_no_unverified_capabilities():
    # prompt, Sprachhint, Streaming und Zeitstempel sind pro Modellfamilie
    # ungeprüft (docs/next/quellen/transcribe-cpp-und-modelle.md) — nichts behaupten.
    assert TranscribeCppBackend().capabilities == SpeechCapabilities(
        prompt=False, language_hint=False, streaming=False, timestamps=False)


def test_without_binary_path_the_backend_is_skipped():
    stub = TranscribeCppBackend()
    assert stub.health() is False
    assert stub.transcribe("clip.wav", OPTS) == SpeechResult(text=None)


def test_health_requires_an_executable_file(tmp_path):
    missing = TranscribeCppBackend(binary=tmp_path / "fehlt")
    assert missing.health() is False
    binary = tmp_path / "transcribe-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    assert TranscribeCppBackend(binary=binary).health() is True
    binary.chmod(0o644)   # nicht ausführbar
    assert TranscribeCppBackend(binary=binary).health() is False


def test_transcribe_runs_the_binary_with_the_wav_path(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="Hallo Welt\n",
                                           stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = TranscribeCppBackend(binary="/opt/transcribe-cli").transcribe(
        "clip.wav", OPTS)
    assert result == SpeechResult(text="Hallo Welt\n")
    cmd, kwargs = calls[0]
    assert cmd == ["/opt/transcribe-cli", "clip.wav"]
    assert kwargs["timeout"] == 7 and kwargs["check"] is False


def test_transcribe_failure_and_nonzero_exit_yield_no_text(monkeypatch):
    def nonzero(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", nonzero)
    stub = TranscribeCppBackend(binary="/opt/transcribe-cli")
    assert stub.transcribe("clip.wav", OPTS) == SpeechResult(text=None)

    def raises(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 7)

    monkeypatch.setattr(subprocess, "run", raises)
    assert stub.transcribe("clip.wav", OPTS) == SpeechResult(text=None)


def test_transcribe_does_not_guess_a_pcm_format(monkeypatch):
    def fail(cmd, **kwargs):
        raise AssertionError("sollte nicht aufgerufen werden")

    monkeypatch.setattr(subprocess, "run", fail)
    stub = TranscribeCppBackend(binary="/opt/transcribe-cli")
    assert stub.transcribe(b"raw pcm", TranscriptionOptions(None)) \
        == SpeechResult(text=None)


def test_warmup_is_an_explicit_no_op():
    assert TranscribeCppBackend(binary="/opt/transcribe-cli").warmup() is False


def test_import_via_backends_package():
    import quassel.backends
    assert quassel.backends.TranscribeCppBackend is TranscribeCppBackend
    assert Path(quassel.backends.__file__).is_file()
