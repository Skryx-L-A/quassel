"""Conservative, opt-in dictation cleanup. No text is sent anywhere by default."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CleanupResult:
    text: str
    used_llm: bool = False
    fallback_reason: str | None = None


_FILLER = re.compile(r"(?i)(?<!\w)(?:äh+|ähm+|aeh+|aehm+|öhm+|oehm+|uh+|umm+|hm+)(?:\.{2,})?(?!\w)[, ]*")
_INITIAL_FILLER = re.compile(r"(?i)^\s*(?:(?:also|sozusagen|you know)\s*[,،:]\s*)+")
_PAIRED_FILLER = re.compile(r"(?i),\s*(?:halt|sozusagen|you know)\s*,\s*")
_REPEAT = re.compile(r"(?i)(?<!\w)([\wÄÖÜäöüß]+)(\s+)\1(?!\w)")
_CORRECTION = re.compile(
    r"(?i)(?<!\w)(?P<first>(?:am|at)\s+[\w:.]+)\s*,?\s*"
    r"(?:nein|sorry|no)\s*,?\s*(?P<second>(?:am|at)\s+[\w:.]+)(?!\w)"
)


def deterministic(text: str) -> str:
    """Remove only clear disfluencies; leave semantic words in place."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    out = _CORRECTION.sub(lambda m: m.group("second"), text)
    out = _INITIAL_FILLER.sub("", out)
    out = _PAIRED_FILLER.sub(", ", out)
    out = _FILLER.sub("", out)
    out = _REPEAT.sub(lambda m: m.group(1), out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    out = re.sub(r"([,;:])\s*([,.!?;:])", r"\2", out)
    out = re.sub(r" {2,}", " ", out).strip()
    return out
