"""Lokaler KI-Backend-Client: optionale Nachbearbeitung diktierten Texts durch
einen LOKALEN Ollama-Server (http://127.0.0.1:11434).

Privatsphaere geht vor: alles bleibt lokal. Der Client wirft NIE an den Aufrufer
und scheitert weich (None / [] / False), damit ein Diktat nie verloren geht oder
blockiert wird. Nur Standardbibliothek (urllib, json, re, threading).
"""
import json
import re
import threading
import urllib.request

OLLAMA_DEFAULT = "http://127.0.0.1:11434"

# Eine einzelne Markdown-Code-Fence-Zeile am Anfang/Ende: ``` oder ```python o.ae.
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*)\n```$", re.DOTALL)
# Drei oder mehr Leerzeilen (vier+ Zeilenumbrueche) -> auf zwei reduzieren.
_BLANKS_RE = re.compile(r"\n{3,}")

# Paare umschliessender Anfuehrungszeichen (gerade und typografisch).
_QUOTE_PAIRS = (
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),  # gerundete doppelte: " "
    ("‘", "’"),  # gerundete einfache: ' '
)


def parse_tags(obj: dict) -> list[str]:
    """Aus einer geparsten /api/tags-Antwort {"models":[{"name":"..."},...]}
    die Liste der Modellnamen zurueckgeben; [] wenn fehlend/fehlerhaft."""
    if not isinstance(obj, dict):
        return []
    models = obj.get("models")
    if not isinstance(models, list):
        return []
    names = []
    for m in models:
        if isinstance(m, dict):
            name = m.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def parse_generate(obj: dict) -> str | None:
    """Aus einer geparsten /api/generate-Antwort {"response":"..."} den String
    zurueckgeben, oder None wenn er fehlt/leer ist."""
    if not isinstance(obj, dict):
        return None
    resp = obj.get("response")
    if isinstance(resp, str) and resp:
        return resp
    return None


def clean_output(text: str) -> str:
    """Eine LLM-Antwort fuer das Einfuegen aufraeumen: umschliessende Leerzeichen
    entfernen; ein einzelnes Paar umschliessender gerader oder typografischer
    Anfuehrungszeichen entfernen, falls der GANZE Text in Anfuehrungszeichen
    steht; eine fuehrende/abschliessende Markdown-Code-Fence (```...```)
    entfernen; 3+ Leerzeilen auf 2 reduzieren. Sonst nichts aendern.
    Leer rein -> "" raus."""
    if not isinstance(text, str):
        return ""
    s = text.strip()
    if not s:
        return ""
    # Code-Fence entfernen (vor dem Anfuehrungszeichen-Schritt; danach neu trimmen).
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    # Genau ein Paar umschliessender Anfuehrungszeichen entfernen.
    for open_q, close_q in _QUOTE_PAIRS:
        if len(s) >= 2 and s[0] == open_q and s[-1] == close_q:
            inner = s[1:-1]
            # Nur entfernen, wenn das Trennzeichen nicht im Inneren wieder vorkommt
            # (sonst war es kein vollstaendig umschliessendes Paar).
            if open_q not in inner and close_q not in inner:
                s = inner.strip()
                break
    # 3+ Leerzeilen auf 2 reduzieren.
    s = _BLANKS_RE.sub("\n\n", s)
    return s


def build_generate_payload(model: str, prompt: str, system: str | None = None,
                           options: dict | None = None) -> dict:
    """JSON-Body fuer POST /api/generate (non-stream) bauen.

    "system" wird nur aufgenommen, wenn es ein nicht-leerer String ist."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options or {"temperature": 0.2},
    }
    if isinstance(system, str) and system:
        payload["system"] = system
    return payload


def available(endpoint: str = OLLAMA_DEFAULT, timeout: float = 2.0) -> bool:
    """GET {endpoint}/api/tags; True genau dann bei HTTP 200.
    Jeder Fehler/Timeout -> False (wirft nie)."""
    url = endpoint.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def list_models(endpoint: str = OLLAMA_DEFAULT, timeout: float = 4.0) -> list[str]:
    """GET {endpoint}/api/tags -> parse_tags. [] bei jedem Fehler (wirft nie)."""
    url = endpoint.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return parse_tags(data)
    except Exception:
        return []


def generate(prompt: str, *, model: str, system: str | None = None,
             endpoint: str = OLLAMA_DEFAULT, timeout: float = 30.0,
             options: dict | None = None) -> str | None:
    """POST {endpoint}/api/generate mit build_generate_payload(...) als JSON.
    Bei HTTP 200 parse_generate -> clean_output. None bei jedem
    Fehler/Timeout/leer (wirft nie). Falsy model -> None."""
    if not model:
        return None
    url = endpoint.rstrip("/") + "/api/generate"
    body = json.dumps(
        build_generate_payload(model, prompt, system, options)
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        resp_text = parse_generate(data)
        if resp_text is None:
            return None
        out = clean_output(resp_text)
        return out or None
    except Exception:
        return None


def generate_async(prompt, callback, *, model, system=None,
                   endpoint=OLLAMA_DEFAULT, timeout=30.0, options=None) -> None:
    """generate(...) in einem Daemon-Thread ausfuehren und callback(result_oder_None)
    aufrufen. Fuer die GUI, damit der Netzwerk-Aufruf die UI nie blockiert."""
    def _run():
        callback(generate(
            prompt, model=model, system=system,
            endpoint=endpoint, timeout=timeout, options=options,
        ))

    threading.Thread(target=_run, daemon=True).start()
