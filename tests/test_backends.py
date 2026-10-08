"""Offline-Vertragstests für das erste SpeechBackend."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import whisperclient
from quassel.backends import (SpeechCapabilities, SpeechResult,
                              TranscriptionOptions, WhisperServerBackend)


def test_whisper_server_backend_declares_current_capabilities():
    backend = WhisperServerBackend()
    assert backend.capabilities == SpeechCapabilities(
        prompt=True, language_hint=True, streaming=False, timestamps=False)


def test_whisper_server_backend_wraps_the_existing_wav_client(monkeypatch):
    calls = []
    monkeypatch.setattr(whisperclient, "_transcribe_wav", lambda *args:
                        calls.append(args) or "Hallo")
    opts = TranscriptionOptions(SimpleNamespace(language="de"), timeout=7,
                                prompt_extra="Quassel", language_hint="de")
    result = WhisperServerBackend().transcribe("dictation.wav", opts)
    assert result == SpeechResult(text="Hallo", language="de")
    assert calls == [("dictation.wav", opts.cfg, 7, "Quassel", [])]


def test_whisper_server_backend_reports_why_it_failed(monkeypatch):
    def fail(path, cfg, timeout, prompt, errors):
        errors.append("timeout")
        return None
    monkeypatch.setattr(whisperclient, "_transcribe_wav", fail)
    result = WhisperServerBackend().transcribe(
        "dictation.wav", TranscriptionOptions(SimpleNamespace(language="de")))
    assert result.text is None and result.error == "timeout"


def test_whisper_server_backend_does_not_guess_a_pcm_format():
    result = WhisperServerBackend().transcribe(b"raw pcm", TranscriptionOptions(None))
    assert result == SpeechResult(text=None)


def test_whisper_server_backend_delegates_health_and_warmup(monkeypatch):
    monkeypatch.setattr(whisperclient, "server_up", lambda: True)
    monkeypatch.setattr(whisperclient, "ensure_server", lambda: True)
    backend = WhisperServerBackend()
    assert backend.health() is True
    assert backend.warmup() is True


def test_compatibility_wrapper_returns_only_the_text(monkeypatch):
    class FakeBackend:
        def transcribe(self, pcm_or_wav, opts):
            assert pcm_or_wav == "dictation.wav"
            assert opts.language_hint == "mixed"
            return SpeechResult(text="text", segments=(object(),), language="de")

    monkeypatch.setattr(whisperclient, "speech_backend", lambda: FakeBackend())
    assert whisperclient.transcribe("dictation.wav", SimpleNamespace(language="mixed")) == "text"
