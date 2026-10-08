"""Small, conservative German/English inverse text normalization rules."""

from __future__ import annotations

import re


_DE = {
    "null": 0, "eins": 1, "ein": 1, "eine": 1, "zwei": 2, "drei": 3,
    "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8,
    "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13,
    "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17,
    "achtzehn": 18, "neunzehn": 19, "zwanzig": 20, "dreißig": 30,
    "vierzig": 40, "fünfzig": 50, "sechzig": 60, "siebzig": 70,
    "achtzig": 80, "neunzig": 90, "hundert": 100,
}
_EN = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}
_MONTH_DE = {m: i for i, m in enumerate((
    "januar februar märz april mai juni juli august september oktober november dezember"
).split(), 1)}
_MONTH_EN = {m: i for i, m in enumerate((
    "january february march april may june july august september october november december"
).split(), 1)}
_ORD_DE = {"erster": 1, "erste": 1, "zweiter": 2, "zweite": 2,
           "dritter": 3, "dritte": 3, "vierter": 4, "vierte": 4,
           "fünfter": 5, "fünfte": 5, "sechster": 6, "sechste": 6,
           "siebter": 7, "siebte": 7, "achter": 8, "achte": 8,
           "neunter": 9, "neunte": 9, "zehnter": 10, "zehnte": 10}
_ORD_EN = {"first": 1, "second": 2, "third": 3, "fourth": 4,
           "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
           "ninth": 9, "tenth": 10}
_WORD = re.compile(r"(?<![\w@./-])[A-Za-zÄÖÜäöüß]+(?:-[A-Za-z]+)?(?![\w@./-])")
_PROTECTED = re.compile(r"```.*?```|`[^`]*`|https?://[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.I | re.S)


def _number(word: str) -> int | None:
    word = word.lower()
    if word in _DE:
        return _DE[word]
    if word in _EN:
        return _EN[word]
    if "und" in word:
        units, tens = word.split("und", 1)
        if units in _DE and _DE[units] < 10 and tens in _DE and 20 <= _DE[tens] <= 90:
            return _DE[units] + _DE[tens]
    if "-" in word:
        tens, units = word.split("-", 1)
        if tens in _EN and _EN[tens] in range(20, 100, 10) and units in _EN and _EN[units] < 10:
            return _EN[tens] + _EN[units]
    return None


_NUMBER_WORD = r"[A-Za-zÄÖÜäöüß]+(?:-[A-Za-z]+)?"
_UNIT = re.compile(rf"(?<![\w@./-])(?P<n>{_NUMBER_WORD})\s+(?P<unit>Prozent|percent|Euro|euros?|Dollar|dollars?|Pfund|pounds?)(?!\w)", re.I)
_TIME_DE = re.compile(rf"(?<!\w)(?P<h>{_NUMBER_WORD})\s+Uhr(?:\s+(?P<m>{_NUMBER_WORD}))?(?!\w)", re.I)
_TIME_EN = re.compile(rf"(?<!\w)(?P<h>{_NUMBER_WORD})\s+o'clock(?!\w)", re.I)
_DATE_DE = re.compile(rf"(?<!\w)(?P<day>{_NUMBER_WORD})\s+(?P<month>{'|'.join(_MONTH_DE)})(?!\w)", re.I)
_DATE_EN = re.compile(rf"(?<!\w)(?P<month>{'|'.join(_MONTH_EN)})\s+(?P<day>{'|'.join(_ORD_EN)})(?!\w)", re.I)


def _normalize_plain(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""

    def de_date(m):
        day = _ORD_DE.get(m.group("day").casefold())
        return f"{day}. {m.group('month')}" if day else m.group()

    def en_date(m):
        day = _ORD_EN.get(m.group("day").casefold())
        return f"{m.group('month')} {day}" if day else m.group()

    def time_de(m):
        hour, minute = _number(m.group("h")), _number(m.group("m")) if m.group("m") else 0
        return f"{hour}:{minute:02d} Uhr" if hour is not None and 0 <= hour <= 23 and minute is not None and 0 <= minute <= 59 else m.group()

    def time_en(m):
        hour = _number(m.group("h"))
        return f"{hour}:00" if hour is not None and 0 <= hour <= 12 else m.group()

    def unit(m):
        n = _number(m.group("n"))
        if n is None:
            return m.group()
        u = m.group("unit").casefold()
        if u in ("prozent", "percent"):
            return f"{n} %"
        if u.startswith("euro"):
            return f"{n} €"
        if u.startswith("dollar"):
            return f"${n}"
        return f"£{n}"

    out = _DATE_DE.sub(de_date, text)
    out = _DATE_EN.sub(en_date, out)
    out = _TIME_DE.sub(time_de, out)
    out = _TIME_EN.sub(time_en, out)
    out = _UNIT.sub(unit, out)

    def simple(m):
        word = m.group()
        # These are commonly pronouns/articles in running text.
        if word.casefold() in ("eins", "ein", "eine", "one"):
            return word
        number = _number(word)
        return str(number) if number is not None else word

    return _WORD.sub(simple, out)


def normalize(text: str) -> str:
    """Convert explicit numeric forms while preserving URLs, addresses and code."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    parts = []
    last = 0
    for match in _PROTECTED.finditer(text):
        parts.append(_normalize_plain(text[last:match.start()]))
        parts.append(match.group())
        last = match.end()
    parts.append(_normalize_plain(text[last:]))
    return "".join(parts)
