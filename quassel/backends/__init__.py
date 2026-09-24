"""Abstraktionen für austauschbare Spracherkennungs-Backends."""
from .base import (SpeechBackend, SpeechCapabilities, SpeechResult,
                   TranscriptionOptions)
from .transcribe_cpp import TranscribeCppBackend
from .whisper_server import WhisperServerBackend

__all__ = ["SpeechBackend", "SpeechCapabilities", "SpeechResult",
           "TranscriptionOptions", "TranscribeCppBackend",
           "WhisperServerBackend"]
