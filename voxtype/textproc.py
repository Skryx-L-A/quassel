"""Nachbearbeitung der Whisper-Transkripte: gesprochene Satzzeichen,
Auto-Großschreibung und Sprachkommandos."""
import re

COMMAND_WORDS = {
    "lösch das", "lösche das", "rückgängig",
    "delete that", "scratch that", "undo",
}

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
    norm = re.sub(r"[^\wäöüß ]", "", text.lower()).strip()
    return norm if norm in COMMAND_WORDS else None


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
