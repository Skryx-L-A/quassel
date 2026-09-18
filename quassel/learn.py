"""Automatisches Lernen von Fachbegriffen fuer das persoenliche Woerterbuch.

Nach einem Diktat werden jargon-/eigennamenartige Tokens erkannt, die Whisper
sonst falsch schreiben wuerde (z. B. "PyTorch", "NASA", "K8s"), und in das
persoenliche Woerterbuch (config.DICTIONARY, ein Wort pro Zeile) aufgenommen.

WICHTIG: Die App ist deutsch+englisch und im Deutschen werden ALLE Substantive
grossgeschrieben. Daher werden gewoehnliche grossgeschriebene Woerter NICHT
gelernt -- nur Tokens, die klar nach Namen/Bezeichnern/Jargon aussehen.

Reine Funktionen ohne Datei-IO; der Aufrufer kuemmert sich um die Persistenz
(config.dictionary_learn liest frisch, fuehrt zusammen und schreibt atomar).
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


def jargon_words(text: str) -> list[str]:
    """Liefert verschiedene Tokens aus `text`, die nach Jargon aussehen.

    Erkannt wird:
      - innere Grossschreibung / CamelCase (z. B. "PyTorch", "VoxType", "macOS")
      - ALL-CAPS Laenge >= 2 (z. B. "NASA", "GPU", "K8S")
      - gemischt Buchstaben+Ziffern (z. B. "K8s", "GPT4", "x86")
    Ausgeschlossen: reine Kleinschreibung, gewoehnliche am-Wortanfang-gross
    geschriebene Woerter (normale Satz-/Substantiv-Grossschreibung), reine
    Zahlen und Tokens kuerzer als 2. Umgebende Satzzeichen werden entfernt.
    Reihenfolge des ersten Auftretens bleibt erhalten, keine Duplikate
    (gross-/kleinschreibungssensitiv unterschieden).
    """
    seen = set()
    out = []
    for raw in text.split():
        tok = raw.strip(_STRIP)
        if not tok or tok in seen:
            continue
        if _is_jargon(tok):
            seen.add(tok)
            out.append(tok)
    return out


def merge_into_dictionary(new_words: list[str], existing: list[str],
                          cap: int = 500) -> list[str]:
    """Liefert die zusammengefuehrte Woerterbuch-Liste.

    Bestehende Woerter zuerst (Reihenfolge erhalten), dann die new_words, die
    noch nicht vorhanden sind (gross-/kleinschreibungs-unabhaengige Dedup gegen
    existing UND innerhalb von new_words), solange die Liste unter cap bleibt.
    Die Obergrenze begrenzt nur, was NEU dazukommt: bestehende Eintraege werden
    nie entfernt. Frueher wurde auf die letzten cap Eintraege gekuerzt, und weil
    die eigenen (manuellen) Woerter vorne stehen, traf das zuerst sie. Ein
    Woerterbuch, das schon ueber cap liegt, bleibt unveraendert. Kein Datei-IO.
    """
    result = list(existing)
    have = {w.lower() for w in existing}
    for w in new_words:
        if cap is not None and len(result) >= cap:
            break
        lw = w.lower()
        if lw in have:
            continue
        have.add(lw)
        result.append(w)
    return result


def learn(text: str, existing: list[str],
          cap: int = 500) -> tuple[list[str], list[str]]:
    """Bequemlichkeit: erkennt Jargon und fuehrt ihn ins Woerterbuch ein.

    Liefert (neues_vollstaendiges_woerterbuch, tatsaechlich_hinzugefuegte_woerter);
    passt wegen cap nicht alles hinein, steht in der zweiten Liste nur, was
    wirklich aufgenommen wurde.
    """
    merged = merge_into_dictionary(jargon_words(text), existing, cap)
    return merged, merged[len(existing):]
