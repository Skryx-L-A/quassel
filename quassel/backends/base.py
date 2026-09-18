"""Kleiner, backendneutraler Vertrag für die Spracherkennung."""
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Tuple


@dataclass(frozen=True)
class SpeechCapabilities:
    prompt: bool
    language_hint: bool
    streaming: bool
    timestamps: bool


@dataclass(frozen=True)
class TranscriptionOptions:
    """Gemeinsame Optionen; ``cfg`` erhält den bisherigen Clientvertrag."""
    cfg: Any
    timeout: float = 120
    prompt_extra: Optional[str] = None
    language_hint: Optional[str] = None


@dataclass(frozen=True)
class SpeechResult:
    text: Optional[str]
    segments: Tuple[Any, ...] = ()
    language: Optional[str] = None


class SpeechBackend(Protocol):
    capabilities: SpeechCapabilities

    def transcribe(self, pcm_or_wav, opts: TranscriptionOptions) -> SpeechResult:
        ...

    def health(self) -> bool:
        ...

    def warmup(self) -> bool:
        ...
