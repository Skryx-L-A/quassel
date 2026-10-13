"""Optional second check for the dictation cleanup: a local typed decision model.

Speaks the Jev wire format (POST /v1/systemone, one "choice" question) to a
service on this machine only, such as the workbench's local decision service.
The cleanup asks it only after the word rules accepted an edit, and keeps the
cleaned text only on a confident "same meaning". Anything else, including an
unreachable service, keeps the raw dictation. Measured 2026-10-09: the rules
let 11 of 20 meaning changes through at the "stark" level; the reference model
judged all 40 pairs correctly.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import ai

# A "yes" below this probability is not trusted. Chosen on the dev pairs of
# the 40-pair set with Decider-4B, the workbench's default local model
# (2026-10-09): the unsafe pair with the highest yes was 0.799 ("might" ->
# "will"), the weakest safe pair 0.907. On the test pairs no unsafe pair got
# through and one safe pair (0.78) fell back to the raw text.
MIN_YES = 0.85

QUESTION = {
    "type": "choice",
    "instructions": (
        "A speech-to-text app cleaned up a raw dictation. Decide whether the cleaned "
        "text says exactly the same thing as the raw dictation. Removing fillers, "
        "repetitions and false starts, resolving a spoken self-correction to its final "
        "version, and fixing punctuation or capitalisation keep the meaning. Any change "
        "of who does what, negation, time, place, amount, certainty or direction changes it."),
    "criteria": {"ja": "same meaning", "nein": "meaning changed", "unklar": "cannot tell"},
}


def usable(url: str) -> bool:
    """Only http(s) on this machine: the dictation never leaves it."""
    try:
        scheme = urllib.parse.urlsplit(url or "").scheme
    except ValueError:
        return False
    return scheme in ("http", "https") and ai.is_loopback(url)


def same_meaning(url: str, raw: str, cleaned: str, timeout: float = 2.0) -> bool | None:
    """True: confident same meaning. False: changed or unsure. None: unavailable."""
    if not usable(url):
        return None
    body = json.dumps({"state": {"raw_dictation": raw, "cleaned_text": cleaned},
                       "model": "jev-latest", "questions": {"q": QUESTION}},
                      ensure_ascii=False).encode("utf-8")
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/v1/systemone"):
        endpoint += "/v1/systemone"
    req = urllib.request.Request(endpoint, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with ai._open(req, endpoint, timeout) as response:
            if response.status != 200:
                return None
            answer = json.loads(response.read())["answers"]["q"]
        choice = answer["choice"]
        probability = float(answer["probabilities"][choice])
    except Exception:   # noqa: BLE001 — network, HTTP error, shape: all mean unavailable
        return None
    return choice == "ja" and probability >= MIN_YES
