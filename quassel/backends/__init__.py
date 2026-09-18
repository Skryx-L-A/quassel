"""Abstraktionen für austauschbare Spracherkennungs-Backends."""
from .base import (SpeechBackend, SpeechCapabilities, SpeechResult,
                   TranscriptionOptions)
from .whisper_server import WhisperServerBackend

__all__ = ["SpeechBackend", "SpeechCapabilities", "SpeechResult",
           "TranscriptionOptions", "WhisperServerBackend"]
