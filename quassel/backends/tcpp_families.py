"""Modellfamilien, Sprachregeln und Fehlerklassen für transcribe.cpp.

Gemeinsam für den CLI-Adapter (transcribe_cpp.py) und die optionale
Python-Bindung (transcribe_cpp_py.py). Jede Aussage hat einen Beleg im
Upstream-Tag ``UPSTREAM_TAG`` (Pfad:Zeile relativ zum Repository
https://github.com/handy-computer/transcribe.cpp):

- Sprachpflicht Cohere/Canary: docs/models/cohere.md:74,
  docs/models/canary.md:77 ("language hint required — no auto-detect").
- Nemotron braucht eine Locale (``de-DE``) oder ``auto``:
  docs/models/nemotron-3.5-asr-streaming-0.6b.md:18-22.
- Parakeet v3 erkennt die Sprache selbst, der Hint ändert nichts:
  docs/models/parakeet.md:122-124; Token-Zeiten: docs/models/parakeet.md:59.
- Voxtral Realtime nur Auto-Erkennung: docs/models/voxtral-realtime.md:32.
- Wörterbuch (vocabulary) und Kontext-Prompt nur Whisper und Qwen3-ASR:
  docs/prompting.md:13-22.
- Streaming nur Nemotron und Voxtral Realtime (unter den hier geführten
  Familien): docs/models/nemotron-3.5-asr-streaming-0.6b.md:6,
  docs/models/voxtral-realtime.md:25.
- Cohere nimmt höchstens rund 400 s: docs/models/cohere.md:44.
- Statustexte: src/transcribe.cpp:124-163.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

UPSTREAM_TAG = "v0.3.1"
UPSTREAM_COMMIT = "3f32fbcc7bb3246851a0234263438bc3c0fa1cac"

# Sprachfälle einer Familie
LANG_OPTIONAL = "optional"     # Auto-Erkennung; de/en dürfen übergeben werden
LANG_REQUIRED = "required"     # ohne -l kein Lauf
LANG_LOCALE = "locale"         # Pflicht, aber als Locale (de-DE) oder "auto"
LANG_AUTO_ONLY = "auto-only"   # nie -l übergeben

# Rückfallsprache, wenn eine Pflicht-Familie keine Einzelsprache bekommt.
FALLBACK_LANGUAGE = "de"

_LOCALES = {"de": "de-DE", "en": "en-US"}


@dataclass(frozen=True)
class Family:
    name: str
    language: str
    vocabulary: bool          # Wörterbuch über --vocabulary(-file)
    context_prompt: bool      # freier Kontext über --prompt
    streaming: bool           # echte Streaming-API (begin/feed/finalize)
    timestamps: bool          # Segment-/Wort-/Token-Zeiten im Ergebnis
    max_audio_s: Optional[float] = None


FAMILIES = {f.name: f for f in (
    Family("whisper", LANG_OPTIONAL, vocabulary=True, context_prompt=True,
           streaming=False, timestamps=True),
    Family("qwen3-asr", LANG_OPTIONAL, vocabulary=True, context_prompt=True,
           streaming=False, timestamps=False),
    Family("cohere", LANG_REQUIRED, vocabulary=False, context_prompt=False,
           streaming=False, timestamps=False, max_audio_s=400.0),
    Family("parakeet", LANG_OPTIONAL, vocabulary=False, context_prompt=False,
           streaming=False, timestamps=True),
    Family("canary", LANG_REQUIRED, vocabulary=False, context_prompt=False,
           streaming=False, timestamps=False),
    Family("nemotron", LANG_LOCALE, vocabulary=False, context_prompt=False,
           streaming=True, timestamps=True),
    Family("voxtral-realtime", LANG_AUTO_ONLY, vocabulary=False,
           context_prompt=False, streaming=True, timestamps=False),
)}

# Bequeme Schreibweisen aus Modell-Dateinamen (handy-computer/*-GGUF).
_ALIASES = {
    "qwen3": "qwen3-asr", "qwen": "qwen3-asr",
    "cohere-transcribe": "cohere",
    "parakeet-tdt": "parakeet", "parakeet-primeline": "parakeet",
    "canary-1b-v2": "canary",
    "nemotron-3.5-asr-streaming": "nemotron",
    "voxtral": "voxtral-realtime",
}


def family(name) -> Family:
    """Familie zu einem Namen; unbekannte Namen sind ein Aufruferfehler."""
    key = (name or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key not in FAMILIES:
        raise ValueError(f"unbekannte transcribe.cpp-Familie {name!r}; "
                         f"bekannt: {', '.join(sorted(FAMILIES))}")
    return FAMILIES[key]


def guess_family(model_path) -> Optional[str]:
    """Familie aus dem Dateinamen raten (nur Vorschlag, nie stilles Raten
    im Diktat: Aufrufer übergeben die Familie ausdrücklich)."""
    base = str(model_path or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for prefix, name in (("whisper", "whisper"), ("qwen3-asr", "qwen3-asr"),
                         ("cohere", "cohere"), ("parakeet", "parakeet"),
                         ("canary", "canary"), ("nemotron", "nemotron"),
                         ("voxtral-mini-4b-realtime", "voxtral-realtime")):
        if base.startswith(prefix):
            return name
    return None


def quant_from_path(model_path) -> Optional[str]:
    """Quantisierung aus dem GGUF-Dateinamen (z. B. ``…-Q8_0.gguf``)."""
    base = str(model_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = base[:-5] if base.lower().endswith(".gguf") else base
    for sep in ("-", ".", "_"):
        head, _, tail = stem.rpartition(sep)
        if head and tail.upper() in _QUANTS:
            return tail.upper()
    for quant in sorted(_QUANTS, key=len, reverse=True):
        if stem.upper().endswith(quant):
            return quant
    return None


_QUANTS = {"F32", "F16", "BF16", "Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S",
           "Q4_K_M", "Q4_K_S", "Q4_0", "Q5_0", "Q5_1"}


def resolve_language(fam: Family, requested) -> Tuple[Optional[str], Tuple[str, ...]]:
    """(Wert für -l oder None, Hinweise) aus Quassels Spracheinstellung.

    Quassel kennt ``de``, ``en``, ``auto`` und ``mixed``. Familien ohne
    Auto-Erkennung bekommen bei ``mixed``/``auto`` die Rückfallsprache
    ``de`` und einen Hinweis, damit das Ergebnis nicht still schlechter wird.
    """
    lang = (requested or "auto").strip().lower()
    single = lang if lang not in ("", "auto", "mixed") else None
    notes = []
    if fam.language == LANG_AUTO_ONLY:
        if single:
            notes.append(f"{fam.name}: nur Auto-Erkennung; Sprache "
                         f"{single!r} nicht übergeben")
        return None, tuple(notes)
    if fam.language == LANG_OPTIONAL:
        return single, ()
    if fam.language == LANG_LOCALE:
        if single is None and lang == "auto":
            return "auto", ()
        if single is None:
            notes.append(f"{fam.name}: Mischsprache nicht unterstützt; "
                         f"Rückfall auf {FALLBACK_LANGUAGE!r}")
            single = FALLBACK_LANGUAGE
        return _LOCALES.get(single, single), tuple(notes)
    # LANG_REQUIRED
    if single is None:
        what = "Mischsprache" if lang == "mixed" else "Auto-Erkennung"
        notes.append(f"{fam.name}: {what} nicht unterstützt; Rückfall auf "
                     f"{FALLBACK_LANGUAGE!r}")
        single = FALLBACK_LANGUAGE
    return single, tuple(notes)


# Fehlerklassen: kurze, stabile Kennungen für Aufrufer und Messberichte.
ERR_NO_BINARY = "no-binary"
ERR_NO_MODEL = "no-model"
ERR_BAD_INPUT = "bad-input"
ERR_SPAWN = "spawn-failed"
ERR_TIMEOUT = "timeout"
ERR_MODEL_LOAD = "model-load"
ERR_LANGUAGE = "unsupported-language"
ERR_TOO_LONG = "input-too-long"
ERR_OOM = "out-of-memory"
ERR_BACKEND = "backend"
ERR_AUDIO = "audio"
ERR_TRUNCATED = "truncated"        # Text liegt vor, aber unvollständig
ERR_REPETITION = "repetition"      # Text liegt vor, Wiederholung abgeschnitten
ERR_EXIT = "nonzero-exit"
ERR_OUTPUT = "bad-output"
ERR_UNAVAILABLE = "unavailable"    # Python-Bindung nicht installiert

# Fehler, bei denen upstream den Teiltext behält (src/transcribe.cpp:160-163).
PARTIAL_ERRORS = (ERR_TRUNCATED, ERR_REPETITION)

_STATUS_PREFIXES = (
    ("output truncated", ERR_TRUNCATED),
    ("output repetition", ERR_REPETITION),
    ("unsupported language", ERR_LANGUAGE),
    ("input audio too long", ERR_TOO_LONG),
    ("out of memory", ERR_OOM),
    ("backend error", ERR_BACKEND),
    ("sample rate mismatch", ERR_AUDIO),
    ("wav:", ERR_AUDIO),
    ("file not found", ERR_MODEL_LOAD),
    ("gguf load error", ERR_MODEL_LOAD),
    ("unsupported architecture", ERR_MODEL_LOAD),
    ("unsupported variant", ERR_MODEL_LOAD),
)


def classify_status(text) -> str:
    """Upstream-Statustext (JSONL-Feld ``error`` oder stderr) → Fehlerklasse."""
    low = (text or "").strip().lower()
    if low.startswith("model load:"):
        return ERR_MODEL_LOAD
    for prefix, cls in _STATUS_PREFIXES:
        if low.startswith(prefix):
            return cls
    return ERR_EXIT
