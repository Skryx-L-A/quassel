"""Metriken des Messrahmens.

WER normalisiert wie die Open-ASR-Leaderboards (Kleinschreibung, ohne
Satzzeichen) — wiederverwendet aus scripts/benchmark_stt_mac.py, damit Messlauf
und Bestandsmessung denselben Wertebegriff nutzen. Dazu zwei "sichtbare"
Fehlerraten, die Leaderboards bewusst wegnormalisieren: Zeichensetzung und
Großschreibung, ausgewertet auf den Wortpaaren eines Alignments der
normalisierten Wortfolgen. Ferner: Wörterbuch-Trefferquote (ganzes Wort, exakt
wiedergegeben), Perzentile mit linearer Interpolation und Kennzahlen zur
Aggregation. Die Halluzinationsprüfung für Stille-Clips nutzt
quassel.dictprompt.is_silence_hallucination (Sperrliste des Produkts).
"""
from __future__ import annotations

import difflib
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from benchmark_stt_mac import PUNCT_TABLE, word_error_rate
except ImportError:  # direkter Aufruf ohne scripts/ im Pfad
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from benchmark_stt_mac import PUNCT_TABLE, word_error_rate

# Exakt die Zeichenmenge, die der normalisierte WER wegstreicht — also die
# sichtbare Zeichensetzung dieses Messrahmens.
_PUNCT_CHARS = "".join(map(chr, PUNCT_TABLE))
_PUNCT_RE = re.compile("[" + re.escape(_PUNCT_CHARS) + "]")

word_error_rate = word_error_rate  # Wiederverwendung, gleicher Wertebegriff


@dataclass(frozen=True)
class WordView:
    """Ein Wort in drei Sichten: normalisiert (WER), sichtbare Satzzeichen
    am Wort, sichtbare Großschreibung."""
    norm: str
    punct: bool
    upper: bool


def word_views(text):
    views = []
    for tok in text.split():
        norm = tok.casefold().translate(PUNCT_TABLE)
        if not norm:
            continue      # eigenständige Satzzeichen tragen kein Wort
        first_letter = next((c for c in tok if c.isalpha()), "")
        views.append(WordView(norm, bool(_PUNCT_RE.search(tok)),
                              bool(first_letter) and first_letter.isupper()))
    return views


def visible_error_rates(reference, hypothesis):
    """``(zeichensetzungs_fehlerrate, grossschreibungs_fehlerrate)``.

    Ausgewertet wird das Alignment der normalisierten Wortfolgen: bei
    gepaarten Wörtern zählt eine Abweichung der sichtbaren Eigenschaft, bei
    fehlenden Referenzwörtern deren verlorene Eigenschaft, bei zusätzlichen
    Ausgabewörtern deren mitgebrachte Eigenschaft. Normiert auf die Zahl der
    Referenzwörter; ohne Referenzwörter None (nicht meßbar).
    """
    ref = word_views(reference)
    hyp = word_views(hypothesis)
    if not ref:
        return (None, None)
    matcher = difflib.SequenceMatcher(a=[w.norm for w in ref],
                                      b=[w.norm for w in hyp], autojunk=False)
    punct_err = cap_err = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            for h in hyp[j1:j2]:
                punct_err += h.punct
                cap_err += h.upper
        elif tag == "delete":
            for r in ref[i1:i2]:
                punct_err += r.punct
                cap_err += r.upper
        else:  # equal / replace
            n = min(i2 - i1, j2 - j1)
            for r, h in zip(ref[i1:i1 + n], hyp[j1:j1 + n]):
                punct_err += r.punct != h.punct
                cap_err += r.upper != h.upper
            for r in ref[i1 + n:i2]:
                punct_err += r.punct
                cap_err += r.upper
            for h in hyp[j1 + n:j2]:
                punct_err += h.punct
                cap_err += h.upper
    total = len(ref)
    return (punct_err / total, cap_err / total)


def dictionary_hit_rate(words, hypothesis):
    """``(treffer, gesamt)``: Wörter aus der Liste, die im Hypothese-Text
    exakt und als ganzes Wort wiedergegeben sind (Großschreibung zählt)."""
    words = [w for w in (words or []) if w]
    if not words:
        return (0, 0)
    hits = sum(1 for w in words
               if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", hypothesis))
    return (hits, len(words))


def percentile(values, pct):
    """Perzentil mit linearer Interpolation; None für leere Listen."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    return ordered[lo] * (hi - rank) + ordered[hi] * (rank - lo)


def summarize_times(values):
    """Median, p95, Minimum und Maximum einer Zeitliste."""
    if not values:
        return {"median": None, "p95": None, "min": None, "max": None}
    return {"median": statistics.median(values), "p95": percentile(values, 95),
            "min": min(values), "max": max(values)}


def mean_or_none(values):
    return sum(values) / len(values) if values else None


def median_or_none(values):
    return statistics.median(values) if values else None
