"""Programmer dictation mode (GitHub issue #25): spoken words -> code text.

Bilingual (English + German) trigger words, since Quassel ships EN/DE. This is a
pure, deterministic string transform with no I/O and no third-party deps.

Grammar
-------
``apply(text)`` walks a spoken sentence left to right and emits code text:

1. Casing spans
   A casing command opens a span that collects the following words and joins
   them in the requested identifier style. The span closes on an explicit
   terminator ("end case", "end", "ende") or at the end of the sentence.

       "camel case foo bar end case"          -> "fooBar"
       "snake case my long name end case"     -> "my_long_name"
       "constant case max size"               -> "MAX_SIZE"   (span runs to end)

   Recognised span openers (longest match wins, e.g. "constant case" before a
   bare "constant"):

       camel    : "camel case", "camelcase", "camel", DE "kamel"
       pascal   : "pascal case", "pascalcase", "pascal", DE "pascalfall"
       snake    : "snake case", "snakecase", "snake", DE "schlange"
       kebab    : "kebab case", "kebabcase", "kebab", DE "spiess"
       constant : "constant case", "constantcase", "constant",
                  "screaming snake", DE "konstante"
       dot      : "dot case", "dotcase", "dot", DE "punktfall"

   Span terminators: "end case", "end <style>" (e.g. "end snake"), "end", DE "ende".
   Words inside a span are taken literally (symbol tokens are NOT expanded there),
   lowercased, and fed to ``to_identifier``.

2. Symbol expansion (outside any span)
   Each ``SYMBOLS`` token is replaced by its literal symbol. Multi-word tokens
   ("open paren", "klammer auf") are matched before single-word tokens
   (longest match first). Unknown words pass through unchanged.

3. Spacing
   Best-effort, readable spacing: no space before ) ] } ; : . , and no space
   after ( [ { . Other tokens are space-joined.

The transform is deterministic and side-effect free.
"""

# ---------------------------------------------------------------------------
# Spoken token -> literal symbol. EN + DE. Multi-word keys are matched before
# single-word keys by apply() (longest match first), so order here is cosmetic.
# ---------------------------------------------------------------------------
SYMBOLS: dict[str, str] = {
    # parentheses / brackets / braces
    "open paren": "(", "klammer auf": "(",
    "close paren": ")", "klammer zu": ")",
    "open brace": "{", "geschweifte klammer auf": "{",
    "close brace": "}", "geschweifte klammer zu": "}",
    "open bracket": "[", "eckige klammer auf": "[",
    "close bracket": "]", "eckige klammer zu": "]",
    # punctuation
    "semicolon": ";", "semikolon": ";",
    "colon": ":", "doppelpunkt": ":",
    "dot": ".", "punkt": ".",
    "comma": ",", "komma": ",",
    "equals": "=", "gleich": "=",
    "arrow": "->", "pfeil": "->",
    "underscore": "_", "unterstrich": "_",
    "dash": "-", "minus": "-", "bindestrich": "-",
    "plus": "+",
    "star": "*", "stern": "*",
    "slash": "/", "schraegstrich": "/", "schrägstrich": "/",
    "pipe": "|",
    "ampersand": "&",
    "hash": "#", "raute": "#",
    "at": "@", "klammeraffe": "@",
    "percent": "%", "prozent": "%",
    "dollar": "$",
    "less than": "<", "kleiner als": "<",
    "greater than": ">", "groesser als": ">", "größer als": ">",
    "bang": "!", "ausrufezeichen": "!",
    "question mark": "?", "fragezeichen": "?",
}

# Casing-span openers: trigger phrase -> style. Longest match wins in apply().
# NOTE: bare single English words ("dot", "snake", ...) are deliberately NOT
# openers, because "dot" collides with the symbol "." (and the others read more
# naturally as plain words). Spans open via the explicit "<style> case" form,
# the joined "<style>case" form, "screaming snake", or a DE alias. The terminator
# "end <style>" still accepts a bare style word (handled in apply()).
_STYLE_OPENERS: dict[str, str] = {
    "camel case": "camel", "camelcase": "camel", "kamel": "camel",
    "pascal case": "pascal", "pascalcase": "pascal", "pascalfall": "pascal",
    "snake case": "snake", "snakecase": "snake", "schlange": "snake",
    "kebab case": "kebab", "kebabcase": "kebab", "spiess": "kebab",
    "constant case": "constant", "constantcase": "constant",
    "screaming snake": "constant", "konstante": "constant",
    "dot case": "dot", "dotcase": "dot", "punktfall": "dot",
}

# Bare style words accepted only as the tail of an "end <style>" terminator.
_END_STYLE_WORDS = {"camel", "pascal", "snake", "kebab", "constant", "dot",
                    "kamel", "schlange", "konstante", "pascalfall", "spiess",
                    "punktfall"}

# Words that close an open casing span.
_END_WORDS = {"end", "ende"}

# No space *before* these; no space *after* these.
# "(" and "[" bind to the preceding token so calls/indexing read as bar() / a[i].
_NO_SPACE_BEFORE = {")", "]", "}", ";", ":", ".", ",", "(", "["}
# "(" "[" "{" open without a trailing space; "." is treated as member access
# (foo.bar) so it also binds tightly to the following token.
_NO_SPACE_AFTER = {"(", "[", "{", "."}


def to_identifier(words: list[str], style: str) -> str:
    """Join *words* into a single identifier in *style*.

    style in {"camel","pascal","snake","kebab","constant","dot"}. Inputs are
    lowercased first.
    """
    parts = [w.lower() for w in words if w]
    if not parts:
        return ""
    if style == "camel":
        return parts[0] + "".join(p.capitalize() for p in parts[1:])
    if style == "pascal":
        return "".join(p.capitalize() for p in parts)
    if style == "snake":
        return "_".join(parts)
    if style == "kebab":
        return "-".join(parts)
    if style == "constant":
        return "_".join(p.upper() for p in parts)
    if style == "dot":
        return ".".join(parts)
    raise ValueError(f"unknown identifier style: {style!r}")


def _match_longest(tokens: list[str], i: int, table: dict[str, str]):
    """Longest phrase in *table* starting at tokens[i].

    Returns (value, consumed_token_count) or (None, 0). Tries up to the longest
    key length so multi-word tokens win over single-word ones.
    """
    max_len = max(len(k.split()) for k in table)
    for n in range(min(max_len, len(tokens) - i), 0, -1):
        phrase = " ".join(tokens[i:i + n]).lower()
        if phrase in table:
            return table[phrase], n
    return None, 0


def _join(pieces: list[str]) -> str:
    """Join *pieces* with readable spacing around brackets/punctuation."""
    out = ""
    for piece in pieces:
        if not piece:
            continue
        if not out:
            out = piece
            continue
        if piece[0] in _NO_SPACE_BEFORE or out[-1] in _NO_SPACE_AFTER:
            out += piece
        else:
            out += " " + piece
    return out


def apply(text: str) -> str:
    """Transform a spoken sentence into code text (see module docstring)."""
    tokens = text.split()
    pieces: list[str] = []

    # Active casing span, if any.
    span_style: str | None = None
    span_words: list[str] = []

    def flush_span():
        nonlocal span_style, span_words
        if span_style is not None:
            pieces.append(to_identifier(span_words, span_style))
        span_style = None
        span_words = []

    i = 0
    n = len(tokens)
    while i < n:
        if span_style is not None:
            # Inside a casing span: look for a terminator, else collect word.
            word = tokens[i].lower()
            if word in _END_WORDS:
                # Consume "end"; also swallow a trailing "case"/style word.
                i += 1
                if i < n and (tokens[i].lower() == "case"
                              or tokens[i].lower() in _END_STYLE_WORDS):
                    i += 1
                flush_span()
                continue
            span_words.append(tokens[i])
            i += 1
            continue

        # Outside a span: casing opener?
        style, consumed = _match_longest(tokens, i, _STYLE_OPENERS)
        if style is not None:
            span_style = style
            span_words = []
            i += consumed
            continue

        # Symbol token?
        sym, consumed = _match_longest(tokens, i, SYMBOLS)
        if sym is not None:
            pieces.append(sym)
            i += consumed
            continue

        # Plain word.
        pieces.append(tokens[i])
        i += 1

    flush_span()  # sentence end closes any open span
    return _join(pieces)
