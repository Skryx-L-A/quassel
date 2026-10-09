"""Automatisches Lernen von Fachbegriffen fuer das persoenliche Woerterbuch.

Nach einem Diktat werden jargon-/eigennamenartige Tokens erkannt, die Whisper
sonst falsch schreiben wuerde (z. B. "PyTorch", "NASA", "K8s"), und in das
persoenliche Woerterbuch (config.DICTIONARY, ein Wort pro Zeile) aufgenommen.

WICHTIG: Die App ist deutsch+englisch und im Deutschen werden ALLE Substantive
grossgeschrieben. Daher werden gewoehnliche grossgeschriebene Woerter NICHT
gelernt -- nur Tokens, die klar nach Namen/Bezeichnern/Jargon aussehen.

Reine Funktionen ohne Datei-IO; der Aufrufer kuemmert sich um die Persistenz
(config.dictionary_learn liest frisch, fuehrt zusammen und schreibt atomar).
Gelernte Woerter landen nicht in dictionary.txt, sondern mit Herkunft in einer
eigenen Datei (siehe "Lebenszyklus" unten).
"""
import re

# Zeichen, die rund um ein Token entfernt werden (Satzzeichen, Klammern, Quotes).
# Bindestrich/Apostroph/Slash bleiben im Wortinneren erhalten.
_STRIP = " \t\r\n.,;:!?\"'`()[]{}<>|*_~=+…„“”‘’«»"


# Zahl + Endung, die nur ein Zahlwort ist: "2nd", "10pm", "5EUR", "100km",
# "20er", "3D", "2,5kg", "10:30pm", "20°C". Ziffer und Buchstabe gemischt sieht
# nach Jargon (K8s, GPT4) aus, ist hier aber keiner.
_NUM_SUFFIX_RE = re.compile(r"^\d+(?:[.,:]\d+)*[°%]?([^\W\d_]+)$")
_NUM_SUFFIXES = frozenset("""
    st nd rd th te ten ter tes tem ste sten ster stes stem
    er d x k
    mm cm dm m km mg g kg t ml cl dl l s ms sec min h hr std uhr
    hz khz mhz ghz w kw mw v kv a ma mah wh kwh kb mb gb tb kib mib gib tib fps
    eur eu usd gbp chf ct am pm pct c f
""".split())
# Gewoehnliche Woerter in Grossbuchstaben, die keine Fachbegriffe sind.
_COMMON_CAPS = frozenset({"OK", "AM", "PM"})


def _has_upper(s: str) -> bool:
    return any(c.isupper() for c in s)


def _has_lower(s: str) -> bool:
    return any(c.islower() for c in s)


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def _is_jargon(tok: str) -> bool:
    """True, wenn tok wie ein zu lernender Fachbegriff aussieht."""
    if len(tok) < 2:
        return False
    if tok in _COMMON_CAPS:
        return False
    m = _NUM_SUFFIX_RE.match(tok)
    if m and m.group(1).lower() in _NUM_SUFFIXES:
        return False  # Zahl + Einheit/Ordinalendung
    letters = [c for c in tok if c.isalpha()]
    digits = [c for c in tok if c.isdigit()]
    if not letters:
        return False  # reine Zahlen / Symbole ausschliessen

    # Buchstaben+Ziffern gemischt: K8s, GPT4, x86
    if digits:
        return True

    # Ab hier reiner Buchstaben-Token.
    if not _has_upper(tok):
        return False  # einfache Kleinschreibung -> kein Jargon

    # ALL-CAPS (Laenge >= 2): NASA, GPU, K8S
    if not _has_lower(tok):
        return True

    # Gemischte Gross-/Kleinschreibung. Gewoehnliche Wortanfangs-Grossschreibung
    # ("Der", "Hund") ausschliessen: nur erstes Zeichen gross, Rest klein.
    # Innere Grossbuchstaben / CamelCase (PyTorch, VoxType, macOS) sind Jargon.
    rest = tok[1:]
    return _has_upper(rest)


def _jargon_in(tok: str) -> list[str]:
    """Jargon in einem Token. Bindestrich-Komposita werden je Teil beurteilt:
    Im Deutschen ist das Glied nach dem Bindestrich gross ("Jeff-Artigen",
    "Stand-Up-Runde"), das ist keine innere Grossschreibung. Bestehen alle
    Teile aus Jargon, Zahlen oder einzelnen Grossbuchstaben ("GPT-4o",
    "COVID-19", "USB-C"), bleibt das Token ganz; sonst nur die Jargon-Teile
    ("PyTorch-Modell" -> "PyTorch")."""
    parts = [p for p in tok.split("-") if p]
    if len(parts) < 2:
        return [tok] if _is_jargon(tok) else []
    jargon = [p for p in parts if _is_jargon(p)]
    if jargon and all(_is_jargon(p) or p.isdigit() or (len(p) == 1 and p.isupper())
                      for p in parts):
        return [tok]
    return jargon


def jargon_words(text: str) -> list[str]:
    """Liefert verschiedene Tokens aus `text`, die nach Jargon aussehen.

    Erkannt wird:
      - innere Grossschreibung / CamelCase (z. B. "PyTorch", "VoxType", "macOS")
      - ALL-CAPS Laenge >= 2 (z. B. "NASA", "GPU", "K8S")
      - gemischt Buchstaben+Ziffern (z. B. "K8s", "GPT4", "x86")
    Ausgeschlossen: reine Kleinschreibung, gewoehnliche am-Wortanfang-gross
    geschriebene Woerter (normale Satz-/Substantiv-Grossschreibung), reine
    Zahlen und Tokens kuerzer als 2. Umgebende Satzzeichen werden entfernt.
    Bindestrich-Komposita siehe _jargon_in.
    Reihenfolge des ersten Auftretens bleibt erhalten, keine Duplikate
    (gross-/kleinschreibungssensitiv unterschieden).
    """
    seen = set()
    out = []
    for raw in text.split():
        tok = raw.strip(_STRIP)
        if not tok:
            continue
        for word in _jargon_in(tok):
            if word not in seen:
                seen.add(word)
                out.append(word)
    return out


# ------------------------------------------------- Lebenszyklus gelernter Wörter
# Gelernte Wörter liegen getrennt vom eigenen Wörterbuch (config.LEARNED), mit
# Herkunft je Eintrag. Ein Wort wirkt erst, wenn es in zwei Diktaten mit
# mindestens MIN_GAP_S Abstand vorkam: eine Fehlhörung, die in einem Gespräch
# dreimal fällt, reicht nicht. Es verfällt, wenn es UNUSED_DAYS nicht mehr
# vorkam, und spätestens nach MAX_ACTIVE_DAYS: ein Wort im Prompt bringt
# Whisper dazu, es wieder zu schreiben, und hielte sich sonst selbst am Leben.
# Wer es behalten will, übernimmt es ins eigene Wörterbuch. Ein entferntes
# Wort kommt auf die Sperrliste und wird nie wieder gelernt.
MIN_GAP_S = 600
CONFIRM_SIGHTINGS = 2
UNUSED_DAYS = 30
MAX_ACTIVE_DAYS = 90
MAX_ENTRIES = 300
MAX_BLOCKED = 500
_DAY = 86400


def empty_state() -> dict:
    return {"version": 1, "words": {}, "blocked": []}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def normalize_state(raw) -> dict:
    """Übernimmt nur gültige Einträge; alles Kaputte fällt weg."""
    state = empty_state()
    if not isinstance(raw, dict):
        return state
    words = raw.get("words")
    if isinstance(words, dict):
        for word, entry in words.items():
            if not (isinstance(word, str) and word.strip() and isinstance(entry, dict)):
                continue
            if not all(_number(entry.get(k)) for k in ("first", "last", "counted", "seen")):
                continue
            active = entry.get("active")
            if active is not None and not _number(active):
                continue
            state["words"][word] = {k: entry[k] for k in ("first", "last", "counted", "seen")}
            state["words"][word]["active"] = active
    blocked = raw.get("blocked")
    if isinstance(blocked, list):
        state["blocked"] = [w for w in blocked if isinstance(w, str) and w.strip()][-MAX_BLOCKED:]
    return state


def _key(state, word):
    folded = word.casefold()
    for known in state["words"]:
        if known.casefold() == folded:
            return known
    return None


def entry(state, word):
    """Eintrag zu word (ohne Rücksicht auf Gross-/Kleinschreibung) oder None."""
    known = _key(state, word)
    return state["words"][known] if known is not None else None


def observe(state, words, now, manual=(), blocked=()) -> list[str]:
    """Zählt die Sichtungen eines Diktats und liefert die Wörter, die dadurch
    neu wirken. Eigene und gesperrte Wörter werden nicht verfolgt."""
    skip = {w.casefold() for w in manual}
    skip |= {w.casefold() for w in blocked}
    skip |= {w.casefold() for w in state["blocked"]}
    added = []
    for word in words:
        if word.casefold() in skip:
            continue
        known = _key(state, word)
        if known is None:
            state["words"][word] = {"first": now, "last": now, "counted": now,
                                    "seen": 1, "active": None}
            item = state["words"][word]
        else:
            item = state["words"][known]
            item["last"] = max(item["last"], now)
            if now - item["counted"] >= MIN_GAP_S:
                item["seen"] += 1
                item["counted"] = now
        if item["active"] is None and item["seen"] >= CONFIRM_SIGHTINGS:
            item["active"] = now
            added.append(known or word)
    return added


def _expired(item, now) -> bool:
    if now - item["last"] > UNUSED_DAYS * _DAY:
        return True
    return item["active"] is not None and now - item["active"] > MAX_ACTIVE_DAYS * _DAY


def active_words(state, now) -> list[str]:
    """Wörter, die jetzt wirken, in der Reihenfolge ihrer Freigabe."""
    items = [(word, item["active"]) for word, item in state["words"].items()
             if item["active"] is not None and not _expired(item, now)]
    return [word for word, _ in sorted(items, key=lambda pair: pair[1])]


def prune(state, now) -> None:
    """Entfernt Verfallenes und hält die Datei klein (zuerst Vorgemerktes)."""
    for word in [w for w, item in state["words"].items() if _expired(item, now)]:
        del state["words"][word]
    if len(state["words"]) > MAX_ENTRIES:
        order = sorted(state["words"].items(),
                       key=lambda kv: (kv[1]["active"] is not None, kv[1]["last"]))
        for word, _ in order[:len(state["words"]) - MAX_ENTRIES]:
            del state["words"][word]


def forget(state, word) -> bool:
    """Entfernt word und sperrt es. True, wenn es ein Eintrag war."""
    known = _key(state, word)
    if known is not None:
        del state["words"][known]
    if word.casefold() not in {w.casefold() for w in state["blocked"]}:
        state["blocked"] = (state["blocked"] + [word])[-MAX_BLOCKED:]
    return known is not None


def drop(state, word) -> bool:
    """Entfernt word ohne Sperre (es wandert ins eigene Wörterbuch)."""
    known = _key(state, word)
    if known is None:
        return False
    del state["words"][known]
    return True
