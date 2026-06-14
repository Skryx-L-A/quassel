"""KI-Modi fuer Quassel: ein "Modus" formt diktierten Text ueber ein LOKALES LLM um.

Eingebaute Modi (#16 Cleanup, #30 Formatierungs-Modi) plus benutzerdefinierte
Modi (#17, name=instruction) und Sprach-Trigger ("turn this into an email").

Privatsphaere/Robustheit zuerst: die Umformung scheitert WEICH. Liefert das LLM
nichts (oder ist der Modus unbekannt, oder der Text leer), bleibt der
Original-Text erhalten -- es geht NIE ein Wort verloren, und es wird NIE geworfen.
Nur Standardbibliothek. Das ai-Modul wird faul importiert.
"""

# key -> {"label_en", "label_de", "system"}. Jeder System-Prompt weist an:
# die Sprache des Nutzers beibehalten; NUR den Ergebnistext ausgeben (kein
# Vorwort, keine Anfuehrungszeichen).
BUILTIN: dict[str, dict] = {
    "cleanup": {
        "label_en": "Clean up",
        "label_de": "Aufraeumen",
        "system": (
            "You clean up dictated text. Remove filler words (um, uh, like, "
            "aeh, oeh) and false starts, fix grammar, spelling and punctuation, "
            "but keep the original meaning and wording as much as possible. "
            "Keep the user's language. Output ONLY the cleaned text, with no "
            "preamble, explanation or quotation marks."
        ),
    },
    "email": {
        "label_en": "As email",
        "label_de": "Als E-Mail",
        "system": (
            "You turn dictated text into a well-structured email: a suitable "
            "greeting, a clear body, and a sign-off. Keep all the user's facts "
            "and intent. Keep the user's language. Output ONLY the email text, "
            "with no preamble, explanation or quotation marks."
        ),
    },
    "bullets": {
        "label_en": "As bullet list",
        "label_de": "Als Liste",
        "system": (
            "You turn dictated text into a tight bullet-point list. Use one "
            "short bullet per point, no duplication, no filler. Keep all the "
            "user's facts. Keep the user's language. Output ONLY the bullet "
            "list, with no preamble, explanation or quotation marks."
        ),
    },
    "paragraphs": {
        "label_en": "As paragraphs",
        "label_de": "Als Absaetze",
        "system": (
            "You organize dictated text into clean, well-separated paragraphs "
            "with proper grammar and punctuation. Keep all the user's facts and "
            "meaning. Keep the user's language. Output ONLY the resulting text, "
            "with no preamble, explanation or quotation marks."
        ),
    },
    "formal": {
        "label_en": "Formal",
        "label_de": "Foermlich",
        "system": (
            "You rewrite dictated text in a formal, polished, professional "
            "register. Keep all the user's facts and intent. Keep the user's "
            "language. Output ONLY the rewritten text, with no preamble, "
            "explanation or quotation marks."
        ),
    },
    "concise": {
        "label_en": "Concise",
        "label_de": "Praegnant",
        "system": (
            "You shorten dictated text to its essential message without losing "
            "any meaning or important facts. Be concise and direct. Keep the "
            "user's language. Output ONLY the shortened text, with no preamble, "
            "explanation or quotation marks."
        ),
    },
}


def builtin_keys() -> list[str]:
    """Die eingebauten Modus-Schluessel in stabiler Reihenfolge."""
    return list(BUILTIN)


def parse_custom(text: str) -> dict[str, str]:
    """Benutzerdefinierte Modi aus mehrzeiligem Text parsen.

    Eine "name=instruction"-Zeile pro Zeile; leere Zeilen und Zeilen, die mit
    '#' beginnen, werden ignoriert; der Name wird getrimmt und auf Kleinschrift
    normalisiert; die Instruction ist alles nach dem ERSTEN '='; leere Namen
    werden uebersprungen; bei Namens-Duplikaten gewinnt der spaetere Eintrag.
    """
    result: dict[str, str] = {}
    if not isinstance(text, str):
        return result
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, instruction = line.split("=", 1)
        name = name.strip().lower()
        if not name:
            continue
        result[name] = instruction.strip()
    return result


def all_modes(custom_text: str = "") -> dict[str, str]:
    """Zuordnung key -> System-Prompt.

    Eingebaute (key -> BUILTIN[key]["system"]) zusammengefuehrt mit
    parse_custom(custom_text). Ein benutzerdefinierter Modus mit gleichem Namen
    ueberschreibt einen eingebauten.
    """
    modes: dict[str, str] = {k: v["system"] for k, v in BUILTIN.items()}
    modes.update(parse_custom(custom_text))
    return modes


def system_for(mode: str, custom_text: str = "") -> str | None:
    """Den System-Prompt fuer `mode` (Schluessel case-insensitiv), sonst None."""
    if not isinstance(mode, str) or not mode:
        return None
    return all_modes(custom_text).get(mode.strip().lower())


# Sprach-Trigger (case-insensitiv, EN + DE) -> eingebauter Modus-Schluessel.
# Pro Modus mehrere Formulierungen. Der laengste Trigger gewinnt (siehe unten).
_VOICE_TRIGGERS: dict[str, str] = {}
for _key, _phrases in {
    "email": (
        "turn this into an email", "make this an email", "as an email",
        "as email", "als e mail", "als e-mail", "als email", "als mail",
    ),
    "bullets": (
        "as a bullet list", "as a list", "as bullets", "als aufzaehlung",
        "als aufzählung", "als liste",
    ),
    "paragraphs": (
        "as paragraphs", "in paragraphs", "in absaetzen", "in absätzen",
    ),
    "formal": (
        "make this formal", "more formal", "foermlich", "förmlich", "formal",
    ),
    "concise": (
        "make it concise", "make this concise", "praegnant", "prägnant",
        "shorter", "kuerzer", "kürzer",
    ),
    "cleanup": (
        "clean this up", "clean up", "aufraeumen", "saeubern", "säubern",
    ),
}.items():
    for _phrase in _phrases:
        _VOICE_TRIGGERS[_phrase] = _key
# Umlaute fuer das Matching auf ASCII-Varianten zurueckfuehren, damit "förmlich"
# und "foermlich" gleich behandelt werden.
_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _norm(s: str) -> str:
    """Nur fuers Matching normalisieren: Kleinschrift + Umlaute auf ASCII."""
    return s.lower().translate(_UMLAUT_MAP)


# Trigger nach normalisierter Laenge absteigend, damit der LAENGSTE zuerst greift.
_NORM_TRIGGERS: list[tuple[str, str]] = sorted(
    ((_norm(phrase), key) for phrase, key in _VOICE_TRIGGERS.items()),
    key=lambda item: len(item[0]),
    reverse=True,
)


def detect_voice_mode(text: str) -> tuple[str | None, str]:
    """Eroeffnet das Diktat mit einer Trigger-Phrase, liefere
    (mode_key, Resttext ohne Trigger und ein folgendes Komma/Doppelpunkt).

    Match case-insensitiv (EN + DE), laengster Trigger zuerst, mit Wortgrenze:
    der Trigger muss die ganze Eroeffnung sein, optional gefolgt von einem
    Leerzeichen/Komma/Doppelpunkt und dann dem Rest. Passt nichts, gib
    (None, text) zurueck. Nur fuers Matching wird normalisiert; der ORIGINALE
    Resttext wird zurueckgegeben. Nur eingebaute Modi.
    """
    if not isinstance(text, str) or not text:
        return (None, text)
    norm = _norm(text)
    for trig, key in _NORM_TRIGGERS:
        if not norm.startswith(trig):
            continue
        rest_norm = norm[len(trig):]
        # Wortgrenze: entweder ist hier das Ende, oder es folgt ein Trenner
        # (Leerraum/Komma/Doppelpunkt). Sonst war der Trigger nur ein Praefix
        # eines laengeren Wortes -> kein Treffer.
        if rest_norm and rest_norm[0] not in " \t\r\n,:":
            continue
        # Im ORIGINAL-Text an der gleichen Stelle abschneiden (normalisierte
        # Laenge == Original-Laenge, da die Umlaut-Map 1->2 Zeichen abbildet?
        # Nein: ae/oe/ue/ss sind 2 Zeichen. Deshalb ueber den Original-String
        # zeichenweise zaehlen statt ueber die normalisierte Laenge.)
        cut = _orig_cut(text, len(trig))
        rest = text[cut:]
        # Fuehrenden Trenner (genau ein Komma/Doppelpunkt und/oder Leerraum)
        # entfernen, dann den Rest links trimmen.
        rest = rest.lstrip()
        if rest[:1] in (",", ":"):
            rest = rest[1:].lstrip()
        return (key, rest)
    return (None, text)


def _orig_cut(text: str, norm_len: int) -> int:
    """Index im ORIGINAL-Text finden, der `norm_len` normalisierten Zeichen
    entspricht. Umlaute zaehlen normalisiert als 2 Zeichen (ae/oe/ue/ss)."""
    consumed = 0
    for i, ch in enumerate(text):
        consumed += len(ch.translate(_UMLAUT_MAP))
        if consumed >= norm_len:
            return i + 1
    return len(text)


def transform(text: str, mode: str, cfg) -> str:
    """Diktierten Text per lokalem LLM gemaess `mode` umformen -- WEICH.

    System-Prompt ueber system_for(mode, <custom-text aus cfg>) holen.
    Unbekannter Modus oder leerer Text -> Original-Text unveraendert.
    Sonst ai.generate(...) aufrufen; ist das Ergebnis truthy, gib es zurueck,
    sonst den ORIGINAL-Text (Fail-Soft). Wirft NIE.
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    custom = getattr(cfg, "ai_modes_text", None)
    if not isinstance(custom, str):
        custom = ""
    system = system_for(mode, custom)
    if not system:
        return text
    try:
        from . import ai
        result = ai.generate(
            text,
            model=getattr(cfg, "ai_model", None),
            system=system,
            endpoint=getattr(cfg, "ai_endpoint", ai.OLLAMA_DEFAULT),
            timeout=getattr(cfg, "ai_timeout", 30.0),
        )
    except Exception:
        return text
    return result if result else text
