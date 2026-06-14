"""Streaming-Tippen: Text erscheint live im Zielfenster (nur Freihand-Modus).

Plattformneutraler Kern — die Tipp-/Lösch-Funktionen werden injiziert
(Linux: Clipboard + Shift+Einfg und ydotool-Backspaces; Windows analog).

Es wird IMMER Wort für Wort getippt (nie mehrere Wörter am Stück): jeder
``type_chunk``-Aufruf trägt genau ein Wort samt umgebender Leerzeichen. So
erscheint der Text Wort für Wort statt in Blöcken; spätere Teiltranskripte
dürfen einzelne Wörter nachträglich per Backspace revidieren.

Modi:
  word       — Standard. Jedes Teilergebnis wird sofort Wort für Wort getippt;
               ändert sich etwas, wird per (gedeckelten) Backspaces korrigiert.
  aggressive — wie ``word`` (Alias, Rückwärtskompatibilität).
  stable     — konservativ: ein Wort wird erst getippt, wenn es in zwei
               aufeinanderfolgenden Teiltranskripten identisch war; während des
               Diktats wird NIE gelöscht (Korrekturen erst im Finale).

Zeilenumbrüche werden nie live getippt (Terminal-Schutz) — sie kommen erst
mit dem finalen Abgleich beim Beenden des Diktats.
"""
import re

AGGRESSIVE_BACKSPACE_CAP = 120   # größere Revisionen warten auf das Finale
EAGER_MODES = ("word", "aggressive")


def _common_word_prefix(a, b):
    """Gemeinsamer Anfang von a und b, abgeschnitten an einer Wortgrenze."""
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    prefix = a[:n]
    if n < len(a) and n < len(b):
        # mitten im Wort divergiert -> letztes (halbes) Wort nicht werten
        cut = prefix.rfind(" ")
        prefix = prefix[:cut + 1] if cut >= 0 else ""
    return prefix


def _live_part(text):
    """Nur der Teil vor dem ersten Zeilenumbruch wird live getippt."""
    return text.split("\n", 1)[0]


def split_words(chunk):
    """Zerlegt ``chunk`` in Einheiten von je einem Wort samt umgebenden
    Leerzeichen. Zusammengefügt ergeben die Einheiten wieder ``chunk``.

    >>> split_words("Hallo Welt wie")
    ['Hallo', ' Welt', ' wie']
    """
    return re.findall(r"\s*\S+|\s+", chunk)


class StreamTyper:
    def __init__(self, mode, type_chunk, delete_chars):
        """type_chunk(text): EIN Wort ins Zielfenster tippen.
        delete_chars(n): n Zeichen per Backspace entfernen."""
        self.mode = mode if mode in EAGER_MODES or mode == "stable" else "word"
        self.type_chunk = type_chunk
        self.delete_chars = delete_chars
        self.typed = ""
        self.prev_partial = None

    def update(self, partial_text):
        """Mit jedem Teiltranskript aufrufen (bereits nachbearbeiteter Text)."""
        live = _live_part(partial_text)
        if self.mode == "stable":
            if self.prev_partial is not None:
                stable = _common_word_prefix(self.prev_partial, live)
                if stable.startswith(self.typed) and len(stable) > len(self.typed):
                    self._emit(stable[len(self.typed):])
                    self.typed = stable
        else:                                   # word / aggressive: sofort, gedeckelt
            self._reconcile(live, cap=AGGRESSIVE_BACKSPACE_CAP)
        self.prev_partial = live

    def finish(self, final_text):
        """Finaler Abgleich: Zielfenster exakt auf den Endtext bringen."""
        self._reconcile(final_text, cap=None)
        return self.typed

    # ------------------------------------------------------------------ intern
    def _emit(self, chunk):
        """Wort für Wort tippen — nie mehrere Wörter in einem Aufruf."""
        for piece in split_words(chunk):
            self.type_chunk(piece)

    def _reconcile(self, target, cap):
        common = 0
        for ca, cb in zip(self.typed, target):
            if ca != cb:
                break
            common += 1
        to_delete = len(self.typed) - common
        if cap is not None and to_delete > cap:
            return                      # zu große Revision -> Finale richtet es
        if to_delete:
            self.delete_chars(to_delete)
            self.typed = self.typed[:common]
        chunk = target[common:]
        if chunk:
            self._emit(chunk)
            self.typed = target
