"""Konservatives Prompt-Budget für whisper.cpp-Wörterbücher.

whisper.cpp behält nur die letzten 224 Prompt-Token.  Diese Näherung braucht
keinen Tokenizer zur Laufzeit und rechnet deutsche Komposita absichtlich zu
teuer, damit Primer und Wake-Phrase am Ende des Prompts erhalten bleiben.
"""
import math


PROMPT_TOKEN_LIMIT = 224
FULL_DICTIONARY_TOKEN_BUDGET = 160
PARTIAL_DICTIONARY_TOKEN_BUDGET = 72


def estimated_tokens(text):
    """Konservative Token-Näherung ohne Modell-Tokenizer.

    Drei UTF-8-Bytes je Token plus ein Token pro Wort decken vor allem lange
    deutsche Komposita und Satzzeichen ab, ohne die Laufzeitabhängigkeit eines
    Tokenizers einzuführen.
    """
    if not text:
        return 0
    return sum(1 + math.ceil(len(word.encode("utf-8")) / 3)
               for word in text.split())


def effective_words(words, token_budget):
    """Die Wörter zurückgeben, die in ``token_budget`` wirklich Platz haben.

    Die Datei wächst durch gelernte Wörter am Ende. Deshalb werden die letzten
    Einträge bevorzugt und in ihrer ursprünglichen Reihenfolge zurückgegeben.
    Diese Funktion ist zugleich die Quelle für eine spätere UI-Anzeige.
    """
    selected = []
    spent = 0
    for word in reversed(words):
        cost = estimated_tokens(word) + 1  # Komma/Leerzeichen im Prompt
        if cost > token_budget - spent:
            continue
        selected.append(word)
        spent += cost
    return list(reversed(selected))


def build_prompt(words, prompt_extra=None, mixed_primer=None, partial=False):
    """``(prompt, wirkende_wörter)`` für die whisper.cpp-Inferenz bauen.

    Zusätze stehen bewusst hinter dem Wörterbuch: bei einem eventuellen
    Upstream-Cut auf die letzten 224 Token bleiben Wake-Phrase und
    Mixed-Primer damit erhalten.
    """
    suffix = [part for part in (prompt_extra, mixed_primer) if part]
    dictionary_limit = (PARTIAL_DICTIONARY_TOKEN_BUDGET if partial
                        else FULL_DICTIONARY_TOKEN_BUDGET)
    available = max(0, PROMPT_TOKEN_LIMIT - estimated_tokens(" ".join(suffix)))
    active_words = effective_words(words, min(dictionary_limit, available))
    parts = [", ".join(active_words), *suffix]
    return " ".join(part for part in parts if part), active_words
