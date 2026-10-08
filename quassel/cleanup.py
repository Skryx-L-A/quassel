"""Conservative, opt-in dictation cleanup. No text is sent anywhere by default."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

from . import itn


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
_WORD = re.compile(r"\b\w+\b", re.UNICODE)
_ENTITY = re.compile(
    r"https?://[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"`[^`]+`|\b[A-Za-z_][A-Za-z_0-9]*_[A-Za-z_0-9]+\b|"
    r"(?<!\w)\d+(?:[.,:/-]\d+)*(?!\w)", re.I
)
_ANSWER = re.compile(r"(?i)^\s*(?:(?:sure|certainly)(?:[,!.:]|\b)|here(?:'s| is)\b|hier ist\b|als (?:ki|ai)\b|as an ai\b|the answer is\b|die antwort ist\b)")
_SENTENCES = re.compile(r"[.!?](?:\s|$)")
_MEANING_WORDS = {"nicht", "nie", "kein", "keine", "ja", "nein", "not", "never",
                  "no", "yes", "heute", "morgen", "gestern", "today", "tomorrow",
                  "yesterday", "before", "after", "vor", "nach"}
LEVELS = ("aus", "leicht", "mittel", "stark")


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


def _replace_terms(text: str, replacements) -> str:
    if not isinstance(replacements, dict):
        return text
    # One pass; a replacement cannot cascade into another rule.
    terms = {k: v for k, v in replacements.items()
             if isinstance(k, str) and isinstance(v, str) and k and v and len(k) <= 100 and len(v) <= 100}
    if not terms:
        return text
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(terms, key=len, reverse=True)) + r")(?!\w)", re.I)
    lookup = {k.casefold(): v for k, v in terms.items()}
    return pattern.sub(lambda m: lookup[m.group().casefold()], text)


def _entities(text: str) -> list[str]:
    return [m.group().rstrip(".,!?;") for m in _ENTITY.finditer(text)]


def _protected_terms(cfg, context) -> list[str]:
    values = getattr(cfg, "cleanup_terms", ())
    if isinstance(context, dict) and "terms" in context:
        values = context["terms"]
    if not isinstance(values, (list, tuple, set)):
        return []
    return [v for v in values if isinstance(v, str) and v.strip() and len(v) <= 100]


def _check(before: str, after: str, level: str, terms: list[str]) -> str | None:
    if not after.strip():
        return "empty"
    before = unicodedata.normalize("NFC", before)
    after = unicodedata.normalize("NFC", after)
    lo, hi = (0.65, 1.35) if level == "mittel" else (0.50, 1.45)
    ratio = len(after) / max(len(before), 1)
    if ratio < lo or ratio > hi:
        return "length"
    if _ANSWER.match(after) and not _ANSWER.match(before):
        return "answer_marker"
    a = [w.casefold() for w in _WORD.findall(before)]
    b = [w.casefold() for w in _WORD.findall(after)]
    if SequenceMatcher(None, a, b, autojunk=False).ratio() < (0.80 if level == "mittel" else 0.70):
        return "word_diff"
    if _entities(before) != _entities(after):
        return "entity_changed"
    if [itn._number(w) for w in a if itn._number(w) is not None] != [itn._number(w) for w in b if itn._number(w) is not None]:
        return "number_changed"
    if [w for w in a if w in _MEANING_WORDS] != [w for w in b if w in _MEANING_WORDS]:
        return "meaning_word_changed"
    for term in terms:
        pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.I)
        if len(pattern.findall(before)) != len(pattern.findall(after)):
            return "dictionary_changed"
    if before.rstrip().endswith("?") and not after.rstrip().endswith("?"):
        return "question_changed"
    added_sentences = len(_SENTENCES.findall(after)) - len(_SENTENCES.findall(before))
    if added_sentences > (0 if before.rstrip().endswith((".", "?", "!")) else 1):
        return "new_sentences"
    return None


def cleanup(raw: str, level: str, cfg, context=None) -> CleanupResult:
    """Fail closed to raw text on every stage error. Explicit switch defaults off."""
    if not isinstance(raw, str):
        return CleanupResult("", fallback_reason="invalid_input")
    if level not in LEVELS:
        return CleanupResult(raw, fallback_reason="invalid_level")
    if level == "aus" or not getattr(cfg, "cleanup_enabled", False) or not raw.strip():
        return CleanupResult(raw)
    try:
        replacements = getattr(cfg, "cleanup_replacements", {})
        pre = deterministic(_replace_terms(raw, replacements))
        if not pre or not 0.35 <= len(pre) / len(raw) <= 1.3:
            return CleanupResult(raw, fallback_reason="prestage_length")
        terms = _protected_terms(cfg, context)
        if isinstance(replacements, dict):
            terms += [v for v in replacements.values() if isinstance(v, str) and v]
        edited = pre
        called = False
        if level in ("mittel", "stark"):
            from . import ai
            called = True
            edited, reason = ai.chat_cleanup(
                pre, model=getattr(cfg, "ai_model", ""),
                endpoint=getattr(cfg, "ai_endpoint", ai.OLLAMA_DEFAULT),
                timeout=getattr(cfg, "ai_timeout", 30.0), level=level)
            if reason or edited is None:
                return CleanupResult(raw, used_llm=called, fallback_reason=reason or "schema_error")
            rejection = _check(pre, edited, level, terms)
            if rejection:
                return CleanupResult(raw, used_llm=called, fallback_reason=rejection)
        final = itn.normalize(edited)
        if not final or not 0.20 <= len(final) / max(len(edited), 1) <= 1.6:
            return CleanupResult(raw, used_llm=called, fallback_reason="itn_length")
        return CleanupResult(final, used_llm=called)
    except Exception:
        return CleanupResult(raw, fallback_reason="processing_error")
