"""Client für den lokalen whisper.cpp-Server (quassel-server.service)."""
import http.client
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
import wave

from . import config, dictprompt, profile

# Windows öffnet sonst für jeden curl-Aufruf kurz ein Konsolenfenster
NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

SERVER = f"http://127.0.0.1:{profile.port()}"
SERVICE = "quassel-server.service"


def _default_starter():
    if sys.platform == "darwin":
        from . import server_mac
        server_mac.start()
        return
    subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)


# Plattform-Hook: Windows ersetzt das durch den eigenen Server-Prozessstart
STARTER = _default_starter


_server_seen = False       # war der Server in dieser Sitzung schon erreichbar?
# Eigener Opener OHNE Proxy: der Server läuft auf 127.0.0.1, und urllib würde
# sonst die Proxy-Einstellungen aus den macOS-Systemeinstellungen anwenden (curl
# las die nie). Mit einem Systemproxy liefe die Probe ins Leere, Quassel hielte
# den laufenden Server für tot und startete ihn neu.
_probe = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def server_up(timeout=2):
    # urllib statt curl-Subprozess: gemessen 0,17ms gegen 5,75ms.
    # Der Multipart-Upload in transcribe() läuft ebenfalls ohne Proxy direkt
    # über die Loopback-Verbindung.
    # ACHTUNG Timeout-Semantik: urllib's timeout gilt je Socket-Operation
    # (Connect, jeder einzelne Read), NICHT für die Gesamtdauer wie curls -m.
    # Auf 127.0.0.1 mit einer winzigen Antwort ist der Unterschied praktisch
    # nicht auslösbar (kein Read kann länger hängen als das ganze Gespräch) —
    # deshalb kein Umbau. Wer diesen Wert später als harte Gesamtfrist liest,
    # irrt sich.
    global _server_seen
    try:
        with _probe.open(SERVER + "/", timeout=timeout) as response:
            server = response.headers.get("Server", "")
        # Ein anderer Dienst auf diesem Port darf den echten Start nicht
        # blockieren und auch nicht als warmer whisper.cpp-Server gelten.
        ok = server.casefold().startswith("whisper.cpp")
    except Exception:  # noqa: BLE001 — jede Ausnahme (Timeout, Refused, HTTP-Fehler) = nicht erreichbar
        ok = False
    if ok:
        _server_seen = True
    return ok


def server_was_up():
    """War der Server in dieser Sitzung schon einmal erreichbar? Dann ist er
    warm und eine kurze Frist reicht. Beim Kaltstart lädt er erst das Modell
    (large-v3-turbo dauert auf schwacher Hardware Minuten) — dort wäre eine
    kurze Frist gleichbedeutend mit einem verworfenen Diktat."""
    return _server_seen


# Vorgabe: die alte Schleife zählte 240 Versuche mit bis zu 2,5 s je Runde, im
# schlechtesten Fall also zehn Minuten. Der Wert hält das für alle Aufrufer,
# die keine eigene Frist mitgeben (Vorladen, Kontrollzentrum, Datei-Transkription).
def ensure_server(deadline=600):
    """Server starten und auf seine Bereitschaft warten, höchstens aber
    deadline Sekunden. Kurze Frist für Pfade, die am Hotkey hängen (das
    Diktat-Ende bei warmem Server), lange für alles andere."""
    if server_up():
        return True
    STARTER()
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if server_up():
            return True
        time.sleep(0.5)
    return False


# Zweisprachiger Anstoß für den "mixed"-Modus: primt das Modell darauf,
# englische Begriffe in deutscher Rede englisch zu lassen (Code-Switching).
MIXED_PRIMER = "Das Meeting ist um 3 PM. Let's go - schick mir das Update."

# Eigene Messung mit ggml-large-v3-turbo-q5_0 auf Apple Silicon (Metal), elf
# Längen, fünf Läufe je Punkt (Median-Serverzeit / Wortfehlerrate). Rohdaten,
# Skripte und Grenzen der Messung liegen im Repo unter
# docs/measurements/2026-08-03-audio-ctx-und-beam-size/:
#
#   Länge   ohne Feld        audio_ctx=768    audio_ctx=1000
#    5,80s  0,484 / 0,000    0,270 / 0,000    0,339 / 0,071
#    9,91s  0,533 / 0,045    0,308 / 0,045    0,391 / 0,091
#   12,62s  0,560 / 0,000    0,349 / 0,000    0,423 / 0,038
#   14,34s  0,581 / 0,034    0,377 / 0,069    0,453 / 0,069
#   16,08s  0,609 / 0,061    0,428 / 0,091    0,491 / 0,121
#   18,03s  0,619 / 0,028    2,368 / 0,667    0,520 / 0,056
#
# Zwei Befunde bestimmen die beiden Konstanten:
#
# 768 hält die Wortfehlerrate bis 12,62s EXAKT auf dem Wert ohne Feld, bei
# 38-44% weniger Serverzeit; ab 14,34s wird sie schlechter, ab 18,03s gerät die
# Dekodierung in eine Wiederholungsschleife und braucht dann sogar länger.
#
# 1000 (rechnerische Reichweite 20s statt 15,4s) würde längere Diktate mit
# abdecken, verschlechtert die Wortfehlerrate aber an JEDEM gemessenen Punkt,
# meist auf das Doppelte — für ~0,1s Zeitgewinn. Genauigkeit vor Geschwindigkeit,
# also nicht genommen.
#
# AUDIO_CTX_MAX_SECONDS liegt deshalb unter 12,62s, dem letzten Punkt mit
# nachweislich unveränderter Wortfehlerrate. Jede Aufnahme, die das Feld
# bekommt, ist damit höchstens so lang wie eine sauber gemessene. Abstand nach
# oben: 2,3s bis zur ersten Verschlechterung, 3,4s bis zum rechnerischen
# Kipppunkt (768/1500 * 30s = 15,36s). NICHT ohne neue Messreihe anheben.
#
# Auf Linux/CUDA gegengemessen (04.08.2026, docs/measurements/
# 2026-08-04-audio-ctx-linux-modelle/): base-q5_1 und small — die Modelle, die
# hwdetect.default_model_for_hardware() ohne NVIDIA wählt — halten die Schwelle
# ebenfalls, mit mehr Abstand als das große Modell. base-q5_1 bleibt bis 14,3s
# unverändert, small bis 16,1s und kippt bis 48s überhaupt nicht. Kleinere
# Modelle sind hier also NICHT empfindlicher.
# Der Kontrolllauf trägt das Ergebnis: large-v3-turbo lieferte auf CUDA/fp16
# bei 16,08s exakt dieselben Werte wie der Mac mit q5_0 (0,061 -> 0,091) und
# bei 18,03s dieselbe Wiederholungsschleife. Andere Hardware, andere
# Quantisierung, gleiches Verhalten.
# OFFEN bleibt Windows: derselbe geteilte Code, dieselben Modellgrößen, aber
# dort hat niemand gemessen.
AUDIO_CTX_SHORT = 768
AUDIO_CTX_MAX_SECONDS = 12.0


def wav_duration_s(wavpath):
    """Dauer einer WAV-Datei in Sekunden, None wenn nicht bestimmbar (Datei
    fehlt/kaputt/kein WAV) — der sichere Zweig für den Aufrufer: kein
    audio_ctx-Feld statt einer geworfenen Ausnahme."""
    try:
        with wave.open(wavpath, "rb") as f:
            rate = f.getframerate()
            if not rate:
                return None
            return f.getnframes() / rate
    except (OSError, wave.Error, EOFError):
        return None


def build_inference_args(wavpath, cfg, words, timeout=120, prompt_extra=None):
    """curl-Argumente für /inference bauen. Sprache:
      auto   -> automatische Erkennung (kein language-Feld)
      mixed  -> automatische Erkennung + zweisprachiger Prompt-Anstoß (#23)
      de/en  -> feste Sprache.
    prompt_extra: zusätzlicher Bias-Text (z.B. das Wake-Word), steht am
    Promptende, damit Whisper z.B. das Kunstwort 'Quassel' nicht wegkürzt.
    Kurze Aufnahmen (< AUDIO_CTX_MAX_SECONDS) bekommen audio_ctx=AUDIO_CTX_SHORT
    mit -- gilt auch für Teiltranskript-Fenster (PartialLoop), dieselbe
    Längenregel dort ist gewollt und billiger."""
    # Nur die WAV geht als Dateiverweis (-F). Alle Textfelder laufen über
    # --form-string: -F deutet einen Wert, der mit "@" oder "<" beginnt, als
    # lokale Datei, und dann scheitert jede Transkription (curl-Fehler 26).
    args = ["curl", "-fsS", "-m", str(timeout), SERVER + "/inference",
            "-F", f"file=@{wavpath}"]
    for name, value in inference_fields(wavpath, cfg, words, timeout, prompt_extra):
        args += ["--form-string", f"{name}={value}"]
    return args


def inference_fields(wavpath, cfg, words, timeout=120, prompt_extra=None):
    """Textfelder für curl- und Speicher-Multipart identisch bestimmen."""
    fields = [("response_format", "text"), ("temperature", "0.0")]
    duration = wav_duration_s(wavpath)
    if duration is not None and duration < AUDIO_CTX_MAX_SECONDS:
        fields.append(("audio_ctx", str(AUDIO_CTX_SHORT)))
    lang = getattr(cfg, "language", "auto")
    if lang not in ("auto", "mixed"):
        fields.append(("language", lang))
    # Der PartialLoop ruft mit genau 20 s auf. Sein kleineres Wörterbuch spart
    # Dekodierzeit, ohne eine Änderung des kompatiblen transcribe()-Aufrufs.
    prompt, _ = dictprompt.build_prompt(
        words, prompt_extra=prompt_extra,
        mixed_primer=MIXED_PRIMER if lang == "mixed" else None,
        partial=timeout == 20)
    if prompt:
        fields.append(("prompt", prompt))
    return fields


def _multipart_body(wavpath, fields):
    """Multipart-Body aus WAV und Textfeldern im Speicher erzeugen."""
    boundary = "----quassel-" + uuid.uuid4().hex
    marker = boundary.encode("ascii")
    body = []
    for name, value in fields:
        body += [b"--" + marker + b"\r\n",
                 (f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                  ).encode("utf-8"),
                 value.encode("utf-8"), b"\r\n"]
    filename = os.path.basename(wavpath).replace('"', "")
    with open(wavpath, "rb") as f:
        wav = f.read()
    body += [b"--" + marker + b"\r\n",
             (f'Content-Disposition: form-data; name="file"; '
              f'filename="{filename}"\r\n').encode("utf-8"),
             b"Content-Type: audio/wav\r\n\r\n", wav, b"\r\n",
             b"--" + marker + b"--\r\n"]
    return b"".join(body), "multipart/form-data; boundary=" + boundary


def _post_multipart(body, content_type, timeout):
    """Ohne Proxy direkt mit dem lokalen whisper.cpp-Server sprechen."""
    parsed = urllib.parse.urlsplit(SERVER)
    if parsed.scheme != "http" or not parsed.hostname:
        return None
    path = (parsed.path.rstrip("/") or "") + "/inference"
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80,
                                      timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers={
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        })
        response = conn.getresponse()
        if not 200 <= response.status < 300:
            return None
        return response.read().decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException, UnicodeError):
        return None
    finally:
        conn.close()


def _transcribe_wav(wavpath, cfg, timeout=120, prompt_extra=None):
    """Bestehende whisper.cpp-Übertragung; intern vom Backend verwendet."""
    words = config.dictionary_words()
    try:
        body, content_type = _multipart_body(
            wavpath, inference_fields(wavpath, cfg, words, timeout, prompt_extra))
    except OSError:
        return None
    result = _post_multipart(body, content_type, timeout)
    if result is not None:
        return result
    # Kompatibler Rückfall, falls die direkte HTTP-Verbindung auf einer alten
    # Installation scheitert. Die Felder bleiben über inference_fields gleich.
    args = build_inference_args(wavpath, cfg, words, timeout, prompt_extra)
    r = subprocess.run(args, capture_output=True, text=True, check=False,
                       encoding="utf-8", errors="replace", **NOWIN)
    return r.stdout if r.returncode == 0 else None


_backend = None


def speech_backend():
    """Die heutige Backend-Wahl, zentral für spätere austauschbare Engines."""
    global _backend
    if _backend is None:
        from .backends import WhisperServerBackend
        _backend = WhisperServerBackend()
    return _backend


def transcribe(wavpath, cfg, timeout=120, prompt_extra=None):
    """Abwärtskompatible dünne Hülle um das Standard-Sprachbackend."""
    from .backends import TranscriptionOptions
    opts = TranscriptionOptions(
        cfg=cfg, timeout=timeout, prompt_extra=prompt_extra,
        language_hint=getattr(cfg, "language", None))
    return speech_backend().transcribe(wavpath, opts).text
