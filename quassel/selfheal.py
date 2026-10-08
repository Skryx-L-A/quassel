"""Bounded functional check for this profile's local speech server."""
import http.client
import io
import sys
import urllib.parse
import wave

from . import profile


def silence_wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0" * 3200)  # 100 ms; no microphone or user data
    return out.getvalue()


def functional_probe(timeout=3):
    """POST a tiny silence clip only to this profile's authenticated path."""
    from . import whisperclient
    if not whisperclient.server_up(timeout=min(timeout, 1)):
        return False
    url = urllib.parse.urlsplit(whisperclient.server_url())
    if url.hostname != "127.0.0.1" or url.port != profile.port():
        return False
    boundary = "quassel-probe"
    body = (b"--quassel-probe\r\n"
            b'Content-Disposition: form-data; name="file"; filename="silence.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n" + silence_wav() +
            b"\r\n--quassel-probe\r\n"
            b'Content-Disposition: form-data; name="response_format"\r\n\r\n'
            b"text\r\n--quassel-probe--\r\n")
    conn = http.client.HTTPConnection("127.0.0.1", profile.port(), timeout=timeout)
    try:
        conn.request("POST", url.path.rstrip("/") + "/inference", body,
                     {"Content-Type": "multipart/form-data; boundary=" + boundary})
        response = conn.getresponse()
        response.read(4096)
        return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def restart_owned():
    """Only restart a server child owned by this process and profile."""
    if sys.platform != "darwin":
        return False
    from . import server_mac
    proc = server_mac._proc
    if proc is None or proc.poll() is not None:
        return False
    server_mac.stop()
    return bool(server_mac.start())


def recover(probe=functional_probe, restart=restart_owned):
    """One bounded recovery attempt; caller may retry inference once."""
    if probe():
        return False
    return bool(restart() and probe())


def report_daemon_crash(app, code):
    """Persist an error and show it even after supervision disabled the pill."""
    from .state import state_set
    message = "Dictation service stopped (exit %s). Turn it on to retry." % code
    app.crash_text = message
    state_set("error", message)
    if app.tray is not None:
        app.tray.set_mode("error", message)
        app.tray.showMessage("Quassel", message)
    if app.pill is not None:
        app.pill.on = True
        app.pill.set_mode("error", message)
