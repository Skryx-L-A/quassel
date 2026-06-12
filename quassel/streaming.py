"""Streaming-Tippen: Text erscheint live im Zielfenster (nur Freihand-Modus).

Plattformneutraler Kern — die Tipp-/Lösch-Funktionen werden injiziert
(Linux: Clipboard + Shift+Einfg und ydotool-Backspaces; Windows analog).

Modi:
  stable     — es werden nur Wortpräfixe getippt, die in zwei
               aufeinanderfolgenden Teiltranskripten identisch waren;
               während des Diktats wird NIE gelöscht
  aggressive — jedes Teilergebnis wird sofort getippt; ändert sich etwas,
               wird per (gedeckelten) Backspaces korrigiert

Zeilenumbrüche werden nie live getippt (Terminal-Schutz) — sie kommen erst
mit dem finalen Abgleich beim Beenden des Diktats.
"""

AGGRESSIVE_BACKSPACE_CAP = 120   # größere Revisionen warten auf das Finale


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


class StreamTyper:
    def __init__(self, mode, type_chunk, delete_chars):
        """type_chunk(text): Häppchen ins Zielfenster tippen.
        delete_chars(n): n Zeichen per Backspace entfernen."""
        self.mode = mode
        self.type_chunk = type_chunk
        self.delete_chars = delete_chars
        self.typed = ""
        self.prev_partial = None

    def update(self, partial_text):
        """Mit jedem Teiltranskript aufrufen (bereits nachbearbeiteter Text)."""
        live = _live_part(partial_text)
        if self.mode == "aggressive":
            self._reconcile(live, cap=AGGRESSIVE_BACKSPACE_CAP)
        else:
            if self.prev_partial is not None:
                stable = _common_word_prefix(self.prev_partial, live)
                if stable.startswith(self.typed) and len(stable) > len(self.typed):
                    chunk = stable[len(self.typed):]
                    self.type_chunk(chunk)
                    self.typed = stable
        self.prev_partial = live

    def finish(self, final_text):
        """Finaler Abgleich: Zielfenster exakt auf den Endtext bringen."""
        self._reconcile(final_text, cap=None)
        return self.typed

    # ------------------------------------------------------------------ intern
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
            self.type_chunk(chunk)
            self.typed = target
