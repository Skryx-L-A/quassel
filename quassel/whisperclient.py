"""Client für den lokalen whisper.cpp-Server (quassel-server.service)."""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave

from . import config

# Windows öffnet sonst für jeden curl-Aufruf kurz ein Konsolenfenster
NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

SERVER = "http://127.0.0.1:8765"
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
    # urllib statt curl-Subprozess: gemessen 0,17ms gegen 5,75ms. Der
    # Multipart-Upload in transcribe() bleibt bei curl (Unterschied dort nur
    # 0,8ms, Umbau riskanter).
    # ACHTUNG Timeout-Semantik: urllib's timeout gilt je Socket-Operation
    # (Connect, jeder einzelne Read), NICHT für die Gesamtdauer wie curls -m.
    # Auf 127.0.0.1 mit einer winzigen Antwort ist der Unterschied praktisch
    # nicht auslösbar (kein Read kann länger hängen als das ganze Gespräch) —
    # deshalb kein Umbau. Wer diesen Wert später als harte Gesamtfrist liest,
    # irrt sich.
    global _server_seen
    try:
        with _probe.open(SERVER + "/", timeout=timeout):
            pass            # Verbindung sofort schließen, nur Erreichbarkeit zählt
        ok = True
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
# GRENZE DER MESSUNG: gemessen wurde EIN Modell auf EINER Plattform. Diese
# Datei ist plattformübergreifend, Linux und Windows bekommen das Feld also
# mit — dort läuft ohne NVIDIA aber small-q5_1 oder base-q5_1
# (hwdetect.default_model_for_hardware), und ob ein kleineres Modell auf ein
# verkürztes Encoder-Fenster gleich reagiert, weiß niemand. Wer die Schwelle
# für eine andere Plattform anpassen will, misst dort zuerst.
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
    prompt_extra: zusätzlicher Bias-Text (z.B. das Wake-Word), wird dem Prompt
    vorangestellt, damit Whisper z.B. das Kunstwort 'Quassel' eher ausgibt.
    Kurze Aufnahmen (< AUDIO_CTX_MAX_SECONDS) bekommen audio_ctx=AUDIO_CTX_SHORT
    mit -- gilt auch für Teiltranskript-Fenster (PartialLoop), dieselbe
    Längenregel dort ist gewollt und billiger."""
    args = ["curl", "-fsS", "-m", str(timeout), SERVER + "/inference",
            "-F", f"file=@{wavpath}",
            "-F", "response_format=text", "-F", "temperature=0.0"]
    duration = wav_duration_s(wavpath)
    if duration is not None and duration < AUDIO_CTX_MAX_SECONDS:
        args += ["-F", f"audio_ctx={AUDIO_CTX_SHORT}"]
    lang = getattr(cfg, "language", "auto")
    if lang not in ("auto", "mixed"):
        args += ["-F", f"language={lang}"]
    prompt_bits = []
    if prompt_extra:
        prompt_bits.append(prompt_extra)
    if lang == "mixed":
        prompt_bits.append(MIXED_PRIMER)
    if words:
        prompt_bits.append(", ".join(words[:80]))
    if prompt_bits:
        args += ["-F", "prompt=" + " ".join(prompt_bits)]
    return args


def transcribe(wavpath, cfg, timeout=120, prompt_extra=None):
    """Transkribiert eine WAV-Datei; None bei Fehler."""
    args = build_inference_args(wavpath, cfg, config.dictionary_words(),
                                timeout, prompt_extra)
    r = subprocess.run(args, capture_output=True, text=True, check=False,
                       encoding="utf-8", errors="replace", **NOWIN)
    return r.stdout if r.returncode == 0 else None
