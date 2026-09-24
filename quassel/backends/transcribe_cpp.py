"""Stub-Adapter für die transcribe.cpp-CLI (Phase 5, docs/next/PLAN.md).

Nur aktiv, wenn ein Binary-Pfad übergeben wird; ohne Pfad liefert health()
False und Aufrufer überspringen das Backend. Die genaue CLI-Syntax ist
ungeprüft (docs/next/quellen/transcribe-cpp-und-modelle.md: "transcribe-cli"
nimmt lokale 16-kHz-Mono-WAV-Dateien) — dieser Stub übergibt den WAV-Pfad als
einziges Argument und liest stdout als Text. Ein echter Adapterprozess mit
Modellwahl, Sprachhint und Zeitmessung ist Teil der Phase-5-Messungen.
"""
import os
import subprocess

from .base import SpeechCapabilities, SpeechResult, TranscriptionOptions
from ..whisperclient import NOWIN


class TranscribeCppBackend:
    capabilities = SpeechCapabilities(prompt=False, language_hint=False,
                                      streaming=False, timestamps=False)

    def __init__(self, binary=None, timeout=120):
        self.binary = os.fspath(binary) if binary else None
        self.timeout = timeout

    def transcribe(self, pcm_or_wav, opts: TranscriptionOptions) -> SpeechResult:
        # Kein Audioformat-Raten: nur echte Dateipfade, kein Raw-PCM.
        if not self.binary or not isinstance(pcm_or_wav, (str, os.PathLike)):
            return SpeechResult(text=None)
        try:
            proc = subprocess.run(
                [self.binary, os.fspath(pcm_or_wav)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", check=False,
                timeout=getattr(opts, "timeout", None) or self.timeout, **NOWIN)
        except (OSError, subprocess.TimeoutExpired):
            return SpeechResult(text=None)
        return SpeechResult(text=proc.stdout if proc.returncode == 0 else None)

    def health(self) -> bool:
        return bool(self.binary and os.path.isfile(self.binary)
                    and os.access(self.binary, os.X_OK))

    def warmup(self) -> bool:
        # Der Stub wärmt nichts vor; False heißt "nicht vorbereitet", kein Fehler.
        return False
