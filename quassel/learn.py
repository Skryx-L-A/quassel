"""Automatisches Lernen von Fachbegriffen fuer das persoenliche Woerterbuch.

Nach einem Diktat werden jargon-/eigennamenartige Tokens erkannt, die Whisper
sonst falsch schreiben wuerde (z. B. "PyTorch", "NASA", "K8s"), und in das
persoenliche Woerterbuch (config.DICTIONARY, ein Wort pro Zeile) aufgenommen.

WICHTIG: Die App ist deutsch+englisch und im Deutschen werden ALLE Substantive
grossgeschrieben. Daher werden gewoehnliche grossgeschriebene Woerter NICHT
gelernt -- nur Tokens, die klar nach Namen/Bezeichnern/Jargon aussehen.

Reine Funktionen ohne Datei-IO; der Aufrufer kuemmert sich um die Persistenz.
"""

# Zeichen, die rund um ein Token entfernt werden (Satzzeichen, Klammern, Quotes).
# Bindestrich/Apostroph/Slash bleiben im Wortinneren erhalten.
_STRIP = " \t\r\n.,;:!?\"'`()[]{}<>|*_~=+…„“”‘’«»"


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

    Bestehende Woerter zuerst (Reihenfolge erhalten), dann alle new_words, die
    noch nicht vorhanden sind (gross-/kleinschreibungs-unabhaengige Dedup gegen
    existing UND innerhalb von new_words). Ueberschreitet das Ergebnis cap, wird
    auf die LETZTEN `cap` Eintraege gekuerzt (juengste behalten). Kein Datei-IO.
    """
    result = list(existing)
    have = {w.lower() for w in existing}
    for w in new_words:
        lw = w.lower()
        if lw in have:
            continue
        have.add(lw)
        result.append(w)
    if cap is not None and len(result) > cap:
        result = result[-cap:]
    return result


def learn(text: str, existing: list[str],
          cap: int = 500) -> tuple[list[str], list[str]]:
    """Bequemlichkeit: erkennt Jargon und fuehrt ihn ins Woerterbuch ein.

    Liefert (neues_vollstaendiges_woerterbuch, tatsaechlich_hinzugefuegte_woerter).
    """
    nw = jargon_words(text)
    have = {w.lower() for w in existing}
    added = [w for w in nw if w.lower() not in have]
    return merge_into_dictionary(nw, existing, cap), added
