"""Ask the user by voice: the next dictation becomes an answer, not text.

A program (CLI `dictate --once`, MCP tool `ask_user`) never starts the
microphone. It only leaves a request in this profile's run directory. The
next dictation the user starts themselves (hotkey or wake phrase) while the
request is waiting is not inserted anywhere: the dictation core hands the
finished text back through an answer file bound to the request's token.

Files (both mode 0600, written atomically):

* ask.request: token, question (shortened, display only), program,
  requesting PID, profile, created, expires. One at a time: a second request
  is refused while a valid one waits.
* ask.claim: token of the request a running dictation answers. It lets the
  asker wait a little past the deadline while the user is still speaking.
* ask.answer: token plus the answer text, or token plus canceled or failed.
  The asker reads it and deletes it at once.

The pattern follows the server restart request in daemon.py
(_request_mac_server_restart) and mac_app.py (handle_server_restart).
The answer text is never logged.
"""
import json
import os
import re
import secrets
import sys
import time

from . import fsutil, i18n, profile, state

REQUEST_NAME = "ask.request"
CLAIM_NAME = "ask.claim"
ANSWER_NAME = "ask.answer"

DEFAULT_TIMEOUT = 120      # s without a user action, then the request ends empty
MAX_TIMEOUT = 600
# s: a dictation that started before the deadline may still finish its answer.
# Matches dictation.ACTION_STALL (the longest the rest of a dictation may run)
# plus the time to speak.
ANSWER_GRACE = 120
QUESTION_MAX = 200         # characters kept in the request and the notification
PILL_QUESTION_MAX = 60     # the pill bubble holds ~140 characters in all
PROGRAM_MAX = 40
ORPHAN_AGE = 30            # s: an answer nobody picked up is removed after this

_TOKEN = re.compile(r"[0-9a-f]{32}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")


class AskError(Exception):
    """reason: busy | timeout | canceled | failed"""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def _paths(rundir=None):
    # RUNDIR is read at call time: profiles and tests change it.
    rundir = state.RUNDIR if rundir is None else rundir
    return (os.path.join(rundir, REQUEST_NAME), os.path.join(rundir, CLAIM_NAME),
            os.path.join(rundir, ANSWER_NAME))


def clamp_timeout(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_TIMEOUT)
    if value != value or value <= 0:     # NaN or nothing sensible
        return float(DEFAULT_TIMEOUT)
    return min(value, float(MAX_TIMEOUT))


def shorten(text, limit):
    """One calm line for display: no control characters, no line breaks."""
    text = _CONTROL.sub(" ", str(text or ""))
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def pid_alive(pid):
    """Does process pid exist? Never sends a signal on Windows: os.kill there
    terminates the process instead of probing it."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == 0x102        # WAIT_TIMEOUT
            finally:
                kernel32.CloseHandle(handle)
        except Exception:   # noqa: BLE001 — without the probe, the deadline still ends it
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_json(path):
    try:
        if os.name != "nt" and os.stat(path).st_uid != os.getuid():
            return None         # not ours: the run directory belongs to this user
        with open(path, encoding="utf-8") as source:
            doc = json.load(source)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _remove_token(path, token):
    """Remove path only while it still belongs to token: a file another
    asker or dictation wrote meanwhile stays."""
    if _token_of(path) == token:
        _remove(path)


def _token_of(path):
    doc = _read_json(path)
    token = doc.get("token") if doc else None
    return token if isinstance(token, str) else None


def _claimed(token, rundir=None):
    _, claim, _ = _paths(rundir)
    return token is not None and _token_of(claim) == token


def _valid(doc, now, rundir=None):
    if not isinstance(doc, dict):
        return False
    token = doc.get("token")
    created, expires = doc.get("created"), doc.get("expires")
    if not (isinstance(token, str) and _TOKEN.fullmatch(token)):
        return False
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in (created, expires)):
        return False
    if not 0 < expires - created <= MAX_TIMEOUT + 1:
        return False
    if doc.get("profile") != profile.name():
        return False
    if not isinstance(doc.get("question"), str) or not isinstance(doc.get("program"), str):
        return False
    if now >= expires and not (_claimed(token, rundir) and now < expires + ANSWER_GRACE):
        return False
    return pid_alive(doc.get("pid"))


def pending(now=None, rundir=None):
    """The waiting request, validated, or None. Reads only, removes nothing:
    the pills call this too."""
    request, _, _ = _paths(rundir)
    doc = _read_json(request)
    now = time.time() if now is None else now
    return doc if _valid(doc, now, rundir) else None


def _sweep_orphan_answer(now, rundir=None):
    """An answer whose asker died before reading it holds dictated text:
    remove it once it is clearly abandoned."""
    _, _, answer = _paths(rundir)
    try:
        age = now - os.stat(answer).st_mtime
    except OSError:
        return
    if age > ORPHAN_AGE:
        _remove(answer)


# ------------------------------------------------------------ asking side
def open_request(question, timeout=DEFAULT_TIMEOUT, program="", pid=None, now=None,
                 rundir=None):
    """Leave a request for the next dictation. Returns the request document.
    Raises AskError("busy") while another valid request waits."""
    request, claim, _ = _paths(rundir)
    now = time.time() if now is None else now
    _sweep_orphan_answer(now, rundir)
    doc = {"token": secrets.token_hex(16),
           "question": shorten(question, QUESTION_MAX),
           "program": shorten(program, PROGRAM_MAX),
           "pid": os.getpid() if pid is None else pid,
           "profile": profile.name(),
           "created": now,
           "expires": now + clamp_timeout(timeout)}
    staging = "%s.%s" % (request, doc["token"])
    try:
        fsutil.atomic_write(staging, json.dumps(doc), mode=0o600)
    except OSError as exc:
        raise AskError("failed", "could not write the request: %s" % type(exc).__name__)
    try:
        for _attempt in range(2):
            try:
                # Hard link: exclusive, so two askers cannot both believe
                # their request is the waiting one.
                os.link(staging, request)
                return doc
            except FileExistsError:
                # Token first, then judge: whatever another asker links in
                # between carries a different token and stays.
                stale = _token_of(request)
                if pending(now, rundir) is not None:
                    raise AskError("busy", "another question is already waiting for an answer")
                _remove_token(request, stale)        # expired, or its asker is gone
                _remove_token(claim, stale)
            except OSError:
                # File system without hard links: check, then replace.
                if pending(now, rundir) is not None:
                    raise AskError("busy", "another question is already waiting for an answer")
                os.replace(staging, request)
                return doc
        raise AskError("busy", "another question is already waiting for an answer")
    finally:
        _remove(staging)


def wait_answer(doc, poll=0.1, sleep=time.sleep, clock=time.time, rundir=None, stop=None):
    """Wait for the answer to request doc. Returns the text. Raises AskError
    with reason timeout (nobody answered), canceled (emergency stop, the
    request vanished, or stop() turned true: the asker itself gave up) or
    busy (another asker replaced it)."""
    request, _, answer = _paths(rundir)
    token = doc["token"]
    while True:
        if stop is not None and stop():
            close_request(doc, rundir)
            raise AskError("canceled", "the question was canceled")
        reply = _read_json(answer)
        if reply and reply.get("token") == token:
            _remove(answer)
            if reply.get("canceled"):
                raise AskError("canceled", "the question was dismissed")
            if reply.get("failed"):
                raise AskError("failed", "the spoken answer could not be transcribed")
            text = reply.get("text")
            return text if isinstance(text, str) else ""
        current = _token_of(request)
        if current is None:
            # The core writes the answer before it removes the request: look once more.
            reply = _read_json(answer)
            if reply and reply.get("token") == token:
                continue
            raise AskError("canceled", "the question was dismissed")
        if current != token:
            raise AskError("busy", "another question replaced this one")
        now = clock()
        if now >= doc["expires"] and not (_claimed(token, rundir)
                                          and now < doc["expires"] + ANSWER_GRACE):
            close_request(doc, rundir)
            raise AskError("timeout", "no answer before the deadline")
        sleep(poll)


def close_request(doc, rundir=None):
    """Remove what belongs to this request (end, timeout, interruption).
    Files of another token stay untouched."""
    request, claim, answer = _paths(rundir)
    token = doc["token"]
    for path in (request, claim, answer):
        if _token_of(path) == token:
            _remove(path)


def ask(question, timeout=DEFAULT_TIMEOUT, program="", notify=None, rundir=None,
        **wait_kwargs):
    """open_request + notification + wait_answer + cleanup in one call."""
    doc = open_request(question, timeout, program, rundir=rundir)
    try:
        if notify is not None:
            try:
                notify(doc)
            except Exception:   # noqa: BLE001 — the pill still shows the question
                pass
        return wait_answer(doc, rundir=rundir, **wait_kwargs)
    finally:
        close_request(doc, rundir)


# --------------------------------------------------------- dictation side
def claim(now=None, rundir=None):
    """At the start of a dictation: if a request waits, this dictation
    answers it. Returns the request document or None. Binding at the start
    (not at the end) keeps text the user began speaking into a window from
    going to a program that asked meanwhile."""
    doc = pending(now, rundir)
    if doc is None:
        return None
    _, claim_path, _ = _paths(rundir)
    token = doc["token"]
    staging = "%s.%s" % (claim_path, secrets.token_hex(8))
    try:
        fsutil.atomic_write(staging, json.dumps({"token": token}), mode=0o600)
    except OSError:
        return doc  # without the claim the asker just does not wait past its deadline
    try:
        for _attempt in range(2):
            try:
                # Hard link: exclusive. A second dictation that starts while
                # the first still works on the answer is not bound to the
                # same request (its text would be lost when the first delivers).
                os.link(staging, claim_path)
                return doc
            except FileExistsError:
                current = _token_of(claim_path)
                if current == token:
                    return None
                _remove_token(claim_path, current)   # left over from an older request
            except OSError:
                # File system without hard links: check, then replace.
                if _claimed(token, rundir):
                    return None
                os.replace(staging, claim_path)
                return doc
        return None
    finally:
        _remove(staging)


def release(token, rundir=None):
    """The dictation that claimed token ended without an answer (canceled,
    too short, nothing heard): the asker no longer waits past its deadline."""
    _, claim_path, _ = _paths(rundir)
    if _token_of(claim_path) == token:
        _remove(claim_path)


def deliver(token, text, now=None, rundir=None):
    """Hand the finished text to the asker of token. False if that request
    no longer waits (deadline passed, emergency stop, asker gone): then the
    text goes nowhere."""
    request, claim_path, answer = _paths(rundir)
    doc = pending(now, rundir)
    if doc is None or doc.get("token") != token:
        return False
    try:
        fsutil.atomic_write(answer, json.dumps({"token": token, "text": text}), mode=0o600)
    except OSError:
        return False
    # Only this request's files: the asker may have ended meanwhile and a
    # new one asked.
    _remove_token(request, token)
    _remove_token(claim_path, token)
    return True


def fail(token, now=None, rundir=None):
    """The dictation that claimed token could not turn the answer into text
    (speech server down, transcription failed): end the question with an
    error at once, instead of letting the asker wait until its deadline.
    False if that request no longer waits."""
    request, claim_path, answer = _paths(rundir)
    doc = pending(now, rundir)
    if doc is None or doc.get("token") != token:
        return False
    try:
        fsutil.atomic_write(answer, json.dumps({"token": token, "failed": True}), mode=0o600)
    except OSError:
        release(token, rundir)          # the asker still ends at its deadline
        return False
    _remove_token(request, token)
    _remove_token(claim_path, token)
    return True


def discard(now=None, rundir=None):
    """Emergency stop: dismiss a waiting request. The asker learns at once
    that it was canceled. Returns True if one was waiting."""
    request, claim_path, answer = _paths(rundir)
    doc = _read_json(request)
    if doc is None:
        _remove(claim_path)
        return False
    token = doc.get("token")
    waiting = _valid(doc, time.time() if now is None else now, rundir)
    if isinstance(token, str) and _TOKEN.fullmatch(token):
        try:
            fsutil.atomic_write(answer, json.dumps({"token": token, "canceled": True}),
                                mode=0o600)
        except OSError:
            pass
    _remove(request)
    _remove(claim_path)
    return waiting


def display(doc, question_max=QUESTION_MAX):
    """The calm line for pill and notification: who asks, what, and how to
    answer. Only the shortened question, never an answer."""
    question = shorten(doc.get("question"), question_max)
    program = shorten(doc.get("program"), PROGRAM_MAX)
    if not question:
        return i18n.tr("ask_waiting", program=program or i18n.tr("ask_a_program"))
    if program:
        return i18n.tr("ask_question", program=program, question=question)
    return i18n.tr("ask_question_anon", question=question)


def pill_line(rundir=None):
    """What the pill shows while a request waits, "" otherwise. Never raises:
    the pill must keep drawing."""
    try:
        doc = pending(rundir=rundir)
        return display(doc, PILL_QUESTION_MAX) if doc is not None else ""
    except Exception:   # noqa: BLE001
        return ""


def notify_user(doc):
    """System notification for a new request, so nobody dictates into the
    wrong place by accident. Windows: the tray app shows the question itself
    (win/app.py WinApp._announce_question), so the asking process does not."""
    if os.name == "nt":
        return False
    from . import platform
    try:
        platform.notify(display(doc))
    except Exception:   # noqa: BLE001 — the pill still shows the question
        return False
    return True


def program_name(pid=None):
    """Name of the process that started the asker (the agent or the shell),
    best effort, for the line „Question from …". Empty if unknown."""
    pid = os.getppid() if pid is None else pid
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/%d/comm" % pid, encoding="utf-8") as source:
                return shorten(source.read(), PROGRAM_MAX)
        except OSError:
            return ""
    if sys.platform == "darwin":
        import subprocess
        try:
            out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=2, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        return shorten(os.path.basename(out.strip()), PROGRAM_MAX)
    return ""
