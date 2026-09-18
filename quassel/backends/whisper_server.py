"""Adapter für den bestehenden lokalen whisper.cpp-Server."""
import os

from .base import SpeechCapabilities, SpeechResult, TranscriptionOptions


class WhisperServerBackend:
    """Erste Backend-Implementierung ohne Änderung des Server-Verhaltens."""
    capabilities = SpeechCapabilities(prompt=True, language_hint=True,
                                      streaming=False, timestamps=False)

    def transcribe(self, pcm_or_wav, opts: TranscriptionOptions) -> SpeechResult:
        # Der vorhandene Server akzeptiert WAV-Dateien. PCM-Unterstützung ist
        # Teil des gemeinsamen Vertrags, braucht aber einen späteren Aufrufer-
        # Umbau, damit kein stiller Audioformat-Ratversuch entsteht.
        if (not isinstance(pcm_or_wav, (str, bytes, os.PathLike))
                or isinstance(pcm_or_wav, bytes)):
            return SpeechResult(text=None)
        from .. import whisperclient
        text = whisperclient._transcribe_wav(
            os.fspath(pcm_or_wav), opts.cfg, opts.timeout, opts.prompt_extra)
        language = opts.language_hint
        return SpeechResult(text=text, language=language)

    def health(self) -> bool:
        from .. import whisperclient
        return whisperclient.server_up()

    def warmup(self) -> bool:
        from .. import whisperclient
        return whisperclient.ensure_server()
