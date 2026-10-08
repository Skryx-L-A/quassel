"""Confirmed-only live dictation with bounded audio windows.

The server currently returns text without word timestamps. A completely
confirmed window can advance the audio anchor. Otherwise the bounded window
slides and its textual overlap is joined to the already confirmed prefix.
"""
import re


def split_words(chunk):
    """Split text into single-word typing operations, preserving whitespace."""
    return re.findall(r"\s*\S+|\s+", chunk)


def _common_word_prefix(left, right):
    """Return identical complete words at the beginning of both hypotheses."""
    words_left = list(re.finditer(r"\S+", left))
    words_right = list(re.finditer(r"\S+", right))
    end = 0
    for a, b in zip(words_left, words_right):
        if left[a.start():a.end()] != right[b.start():b.end()]:
            break
        end = b.end()
    return right[:end]


def _after_overlap(confirmed, window_text):
    """Remove the longest word overlap at a shifted window's beginning."""
    old = [m.group() for m in re.finditer(r"\S+", confirmed)]
    new = list(re.finditer(r"\S+", window_text))
    for count in range(min(len(old), len(new)), 0, -1):
        if old[-count:] == [m.group() for m in new[:count]]:
            return window_text[new[count - 1].end():]
    return window_text


class StreamTyper:
    """Confirm a word after two consecutive hypotheses agree on its prefix."""

    def __init__(self, mode, type_chunk, delete_chars):
        # Saved mode names remain accepted. Eager corrections caused B25.
        self.mode = mode if mode in ("word", "aggressive", "stable") else "word"
        self.type_chunk = type_chunk
        self.delete_chars = delete_chars
        self.typed = ""
        self.prev_partial = None
        self.window_start = None
        self.window_base = ""
        self.last_audio_end = 0
        self.stable_audio_end = 0

    def audio_window(self, total_bytes, max_bytes, overlap_bytes=32000):
        """Choose a bounded window starting at the last safe audio anchor."""
        return max(0, self.stable_audio_end - overlap_bytes,
                   total_bytes - max_bytes)

    def prompt_context(self):
        """Short confirmed-text context for the next recognition request."""
        return self.typed[-160:].lstrip() or None

    def update(self, partial_text, audio_start=None, audio_end=None):
        """Process a partial result; blank/error results leave state untouched."""
        if not partial_text:
            return
        live = partial_text.split("\n", 1)[0].strip()
        if not live:
            return
        if audio_start is not None and audio_start != self.window_start:
            self.window_start = audio_start
            self.window_base = self.typed
        if audio_start is not None:
            remainder = _after_overlap(self.window_base, live)
            if remainder and self.window_base and not remainder[0].isspace():
                remainder = " " + remainder
            candidate = self.window_base + remainder
        else:
            candidate = live

        if self.prev_partial is not None:
            stable = _common_word_prefix(self.prev_partial, candidate)
            if stable.startswith(self.typed) and len(stable) > len(self.typed):
                self._emit(stable[len(self.typed):])
                self.typed = stable
            if (audio_end is not None and candidate == self.prev_partial
                    and self.last_audio_end):
                self.stable_audio_end = max(self.stable_audio_end,
                                            self.last_audio_end)
        self.prev_partial = candidate
        if audio_end is not None:
            self.last_audio_end = audio_end

    def finish(self, final_text):
        """Append only the unconfirmed final remainder; never erase live text."""
        if not final_text:
            return self.typed
        if final_text.startswith(self.typed):
            remainder = final_text[len(self.typed):]
        else:
            remainder = _after_overlap(self.typed, final_text)
            if remainder == final_text:
                return self.typed
            if remainder and self.typed and not remainder[0].isspace():
                remainder = " " + remainder
        self._emit(remainder)
        self.typed += remainder
        return self.typed

    def _emit(self, chunk):
        for piece in split_words(chunk):
            self.type_chunk(piece)
