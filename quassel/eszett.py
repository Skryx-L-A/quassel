"""„ss" zurück zu „ß", wo die deutsche Rechtschreibung es verlangt.

Das deutsche Turbo-Finetune (primeLine) schreibt nie „ß", sondern immer „ss"
(gemessen 2026-10-09: 0 von 22). Die Regel hier ändert ein Wort nur, wenn es
mit „ss" kein Wort ist und genau eine ß-Form eines ist („grosses" -> „großes",
„heisst" -> „heißt"); gültige ss-Wörter („dass", „Masse", „Fluss") bleiben.
Englische Wörter („class", „business") haben keine ß-Form und bleiben ebenfalls.
Gemessen auf denselben Daten: 18 von 19 ß-Wörtern zurück, kein falsches ß,
keine Änderung an korrekten Referenztexten, an Turbo oder an Parakeet.

Schweizer Rechtschreibung kennt kein „ß"; deshalb steht die Regel hinter
``[text] eszett`` (auto = nur mit einem deutschen Finetune-Modell).
"""
import itertools
import os
import re

_TOKEN = re.compile(r"[A-Za-zÄÖÜäöüß]+")
_SS = re.compile(r"(?i)ss")
_MIN_TAIL = 5
_data = None


def _words():
    global _data
    if _data is None:
        from . import eszett_data
        _data = (eszett_data.ESZETT, eszett_data.SS_WORDS)
    return _data


def _known_eszett(word, eszett):
    return word in eszett or (word[:1].isupper() and word.lower() in eszett)


def _known_ss(word, ss_words):
    return word in ss_words or (word[:1].isupper() and word.lower() in ss_words)


def restore_word(word):
    if "ss" not in word.lower():
        return word
    eszett, ss_words = _words()
    if _known_ss(word, ss_words):
        return word
    spots = [m.start() for m in _SS.finditer(word)]
    variants = set()
    for n in range(1, len(spots) + 1):
        for combo in itertools.combinations(spots, n):
            candidate = word
            for pos in sorted(combo, reverse=True):
                candidate = candidate[:pos] + "ß" + candidate[pos + 2:]
            if _known_eszett(candidate, eszett):
                variants.add(candidate)
    if len(variants) == 1:
        return variants.pop()
    if variants:
        return word                       # mehrdeutig: nichts raten
    # Zusammensetzung („hinfliesst"): das kürzeste Endstück ab fünf Zeichen,
    # das das letzte ss enthält und dessen ß-Form ein Wort ist.
    last = spots[-1]
    for start in range(last, -1, -1):
        tail = word[start:]
        if len(tail) < _MIN_TAIL:
            continue
        if _known_ss(tail, ss_words):
            break
        candidate = tail[:last - start] + "ß" + tail[last - start + 2:]
        if _known_eszett(candidate, eszett):
            return word[:start] + candidate
    return word


def restore(text):
    """Text mit wiederhergestelltem ß; ändert nur einzelne Wörter."""
    if "ss" not in text.lower():
        return text
    return _TOKEN.sub(lambda m: restore_word(m.group(0)), text)


def enabled(setting, model_path, language):
    """``setting``: on | off | auto. auto = nur, wenn das eingestellte Modell ein
    deutsches Finetune ist (Dateiname mit „german"), und nie für Englisch."""
    if (language or "").lower().startswith("en"):
        return False
    setting = (setting or "auto").lower()
    if setting == "on":
        return True
    if setting == "off":
        return False
    return "german" in os.path.basename(model_path or "").lower()
