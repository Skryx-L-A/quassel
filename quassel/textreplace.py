"""Textersetzung / Snippet-Erweiterung (GitHub-Issue #20).

Der Nutzer definiert Kurzbefehle, die im final diktierten Text expandieren,
z. B. ``omw=on my way`` oder ``myemail=lillebor.alberti.l@gmail.com``.
Regeln liegen zeilenweise als ``trigger=expansion`` in einer Textdatei.

Dieses Modul ist reine Logik: ``apply_rules`` macht kein Datei-IO, der Aufrufer
uebergibt bereits geparste Regeln. ``parse_rules`` liest den Regeltext,
``expand`` ist die Bequemlichkeits-Kombination aus beiden.
"""
import re

__all__ = ["parse_rules", "apply_rules", "expand"]


def parse_rules(text):
    """Regeltext -> Liste von (trigger, expansion).

    Eine Regel pro Zeile ``trigger=expansion``. Leerzeilen und Zeilen, die mit
    ``#`` beginnen, werden ignoriert. Der Trigger wird beidseitig getrimmt; die
    Expansion bleibt nach dem ersten ``=`` unveraendert (auch ihre Leerzeichen).
    Zeilen mit leerem Trigger werden uebersprungen. Ein spaeterer doppelter
    Trigger ueberschreibt einen frueheren (Reihenfolge bleibt, letzter gewinnt).
    """
    out = []
    index = {}  # trigger -> Position in out (fuer "letzter gewinnt")
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        trigger, expansion = line.split("=", 1)
        trigger = trigger.strip()
        if not trigger:
            continue
        if trigger in index:
            out[index[trigger]] = (trigger, expansion)
        else:
            index[trigger] = len(out)
            out.append((trigger, expansion))
    return out


def _carry_capitalization(matched, expansion):
    """Wenn das gematchte Wort gross anfaengt und die Expansion klein, dann
    den ersten Buchstaben der Expansion grossschreiben ("Omw," -> "On my way,").
    """
    if matched and matched[0].isupper() and expansion and expansion[0].islower():
        return expansion[0].upper() + expansion[1:]
    return expansion


def apply_rules(text, rules, *, case_insensitive=True):
    """Ersetzt ganzwortige Vorkommen jedes Triggers durch seine Expansion.

    Ganzwortig = an Wortgrenzen / Stringenden begrenzt (Regex mit ``\\b``-artigen
    Grenzen, sicher via :func:`re.escape` gebaut). Bei ``case_insensitive``
    wird unabhaengig von Gross-/Kleinschreibung gematcht; war das gematchte Wort
    grossgeschrieben und die Expansion klein, wird der erste Buchstabe der
    Expansion grossgeschrieben.

    Laengere Trigger werden vor kuerzeren angewandt, um teilweises Verschatten
    zu vermeiden. Mehrwort-Trigger (mit Leerzeichen) matchen ebenfalls.
    """
    if not rules:
        return text

    # Letzten Wert je Trigger nehmen (defensiv, falls Aufrufer Duplikate liefert).
    resolved = {}
    for trigger, expansion in rules:
        resolved[trigger] = expansion

    # Laengere Trigger zuerst (verhindert, dass "cat" das Wort in "cat nap"
    # vor "cat nap" abfaengt). Stabil bei Gleichstand: alphabetisch.
    ordered = sorted(resolved.items(), key=lambda kv: (-len(kv[0]), kv[0]))

    flags = re.IGNORECASE if case_insensitive else 0
    for trigger, expansion in ordered:
        if not trigger:
            continue
        # \b funktioniert nur an Wort-/Nichtwort-Uebergaengen. Trigger koennen mit
        # Nichtwort-Zeichen anfangen/enden (z. B. ":)"), daher Grenzen via
        # Lookaround auf Wortzeichen statt blindem \b setzen.
        esc = re.escape(trigger)
        left = r"(?<!\w)" if trigger[0].isalnum() or trigger[0] == "_" else ""
        right = r"(?!\w)" if trigger[-1].isalnum() or trigger[-1] == "_" else ""
        pattern = re.compile(left + esc + right, flags)

        def _sub(match, _expansion=expansion):
            # Funktion als Ersetzung -> Rueckgabe wird literal eingesetzt,
            # Backslashes/Gruppen-Refs werden NICHT interpretiert (kein Maskieren).
            return _carry_capitalization(match.group(0), _expansion)

        text = pattern.sub(_sub, text)
    return text


def expand(text, rules_text, **kw):
    """Bequemlichkeit: ``parse_rules(rules_text)`` und dann ``apply_rules``."""
    return apply_rules(text, parse_rules(rules_text), **kw)
