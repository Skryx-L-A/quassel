"""Before the paste keystroke: wait until the clipboard holds the text.

The platforms used fixed pauses (macOS and Linux 250 ms, Windows 150 ms,
streaming 120 or 80 ms) so that the clipboard held the new text before the
keystroke. This checks exactly that and stops as soon as it is true; the old
pause stays the upper bound for a clipboard that never reads back the text.
"""
import time

POLL_S = 0.01


def wait_until_holds(read, text, max_s, now=time.monotonic, sleep=time.sleep):
    """Poll read() until it returns text, at most max_s. Returns seconds waited."""
    start = now()
    deadline = start + max(0.0, max_s)
    while True:
        try:
            ready = read() == text
        except Exception:   # noqa: BLE001 — unreadable counts as not yet ready
            ready = False
        if ready or now() >= deadline:
            break
        sleep(min(POLL_S, deadline - now()))
    return now() - start
