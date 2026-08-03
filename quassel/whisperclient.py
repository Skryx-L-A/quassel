"""Client für den lokalen whisper.cpp-Server (quassel-server.service)."""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

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


def build_inference_args(wavpath, cfg, words, timeout=120, prompt_extra=None):
    """curl-Argumente für /inference bauen. Sprache:
      auto   -> automatische Erkennung (kein language-Feld)
      mixed  -> automatische Erkennung + zweisprachiger Prompt-Anstoß (#23)
      de/en  -> feste Sprache.
    prompt_extra: zusätzlicher Bias-Text (z.B. das Wake-Word), wird dem Prompt
    vorangestellt, damit Whisper z.B. das Kunstwort 'Quassel' eher ausgibt."""
    args = ["curl", "-fsS", "-m", str(timeout), SERVER + "/inference",
            "-F", f"file=@{wavpath}",
            "-F", "response_format=text", "-F", "temperature=0.0"]
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
