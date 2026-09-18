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
    (r"semikolon", ";"),
    (r"question mark", "?"), (r"exclamation (?:mark|point)", "!"),
    (r"full stop", "."),
    (r"colon", ":"), (r"semicolon", ";"),
]

_NUMBER_WORDS = {
    "null", "eins", "ein", "eine", "einen", "einem", "einer", "zwei", "drei",
    "vier", "fuenf", "fünf", "sechs", "sieben", "acht", "neun", "zehn",
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten",
}


def _is_number_word(word):
    return word.isdigit() or word.lower() in _NUMBER_WORDS


def _replace_spoken_comma(match):
    """Replace a comma word unless it is the decimal separator in spoken prose."""
    text = match.string
    before = re.search(r"(\w+)\W*$", text[:match.start()])
    after = re.match(r"\W*(\w+)", text[match.end():])
    if before and after and _is_number_word(before.group(1)) and _is_number_word(after.group(1)):
        return match.group(0)
    return ","


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
    # "Punkt" und "period" sind normale Wörter. Als Satzzeichen gelten sie
    # deshalb nur am Ende der Äußerung; sonst bleibt z. B. "wichtiger Punkt"
    # unverändert.
    text = re.sub(r"\s*\b(?:punkt|period)\b[\s,.!?]*$", ".", text,
                  flags=re.IGNORECASE)
    # "zwei Komma fünf" beschreibt eine Zahl, kein Satzzeichen.
    text = re.sub(r"\s*\b(?:komma|comma)\b", _replace_spoken_comma, text,
                  flags=re.IGNORECASE)
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
        # Nur vor einem Großbuchstaben beginnt sicher ein neuer Satz. So bleiben
        # Domains, Dateinamen und Abkürzungen wie github.com oder e.g. ganz.
        text = re.sub(r"([,.!?:;])(?=[A-ZÀ-ÖØ-Þ])", r"\1 ", text)
        text = "\n".join(line.strip() for line in text.split("\n"))
    return "text", text
