"""Nachbearbeitung der Whisper-Transkripte: gesprochene Satzzeichen,
Auto-Großschreibung und Sprachkommandos."""
import re

# Ganzes Diktat == Phrase -> Kommando (action). Phrasen normalisiert (lower,
# ohne Satzzeichen). Längere/spezifische Phrasen brauchen keine Sonderordnung,
# da exakt-Match gegen das gesamte Transkript geprüft wird.
COMMANDS = {
    # letztes Diktat löschen
    "lösch das": "undo", "lösche das": "undo", "rückgängig": "undo",
    "delete that": "undo", "scratch that": "undo", "undo": "undo",
    # Eingabetaste drücken (#32)
    "press enter": "enter", "hit enter": "enter", "enter": "enter",
    "drück enter": "enter", "drücke enter": "enter", "enter drücken": "enter",
    "neue zeile drücken": "enter",
}
# Rückwärtskompatibilität: Menge aller Kommando-Phrasen
COMMAND_WORDS = set(COMMANDS)

# Reihenfolge wichtig: längere Phrasen zuerst
PUNCT_RULES = [
    (r"neuer absatz", "\n\n"), (r"neue zeile", "\n"), (r"zeilenumbruch", "\n"),
    (r"new paragraph", "\n\n"), (r"new line", "\n"), (r"newline", "\n"),
    (r"fragezeichen", "?"), (r"ausrufezeichen", "!"), (r"doppelpunkt", ":"),
    (r"semikolon", ";"), (r"komma", ","), (r"punkt", "."),
    (r"question mark", "?"), (r"exclamation (?:mark|point)", "!"),
    (r"full stop", "."), (r"period", "."), (r"comma", ","),
    (r"colon", ":"), (r"semicolon", ";"),
]


def is_command(text):
    """-> action ('undo' | 'enter') wenn das ganze Diktat ein Kommando ist, sonst None."""
    norm = re.sub(r"[^\wäöüß ]", "", text.lower()).strip()
    norm = " ".join(norm.split())
    return COMMANDS.get(norm)


def apply_punctuation(text):
    for pat, repl in PUNCT_RULES:
        if repl.startswith("\n"):
            # Umbruch ersetzt das Wort samt umgebender Leerzeichen/Satzzeichen
            text = re.sub(rf"\s*\b{pat}\b[\s,.]*", repl, text, flags=re.IGNORECASE)
        else:
            # Satzzeichen hängt sich ans vorherige Wort
            text = re.sub(rf"\s*\b{pat}\b", repl, text, flags=re.IGNORECASE)
    return text


def auto_capitalize(text):
    def up(m):
        return m.group(1) + m.group(2).upper()
    return re.sub(r"(^|[.!?]\s+|\n\s*)([a-zäöü])", up, text)


def postprocess(raw_text, cfg):
    """-> ('command', name) | ('text', fertiger_text) | (None, None)"""
    text = " ".join(raw_text.split()).strip()
    if not text or re.fullmatch(r"[\[\(\*].*[\]\)\*]", text):
        return None, None
    if cfg.commands:
        cmd = is_command(text)
        if cmd:
            return "command", cmd
    if cfg.punctuation:
        text = apply_punctuation(text)
        text = auto_capitalize(text)
        # Leerzeichen hinter Satzzeichen sicherstellen, Zeilen sauber trimmen
        text = re.sub(r"([,.!?:;])(?=[A-Za-zÄÖÜäöü])", r"\1 ", text)
        text = "\n".join(line.strip() for line in text.split("\n"))
    return "text", text
