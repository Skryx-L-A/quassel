"""Tests des lokalen KI-Backend-Clients (kein laufendes Ollama, kein Internet).

Reine Helfer (Payload-Bau, Output-Reinigung, Antwort-/Tags-Parser) werden direkt
geprueft. available()/list_models() werden nur gegen einen garantiert toten
Loopback-Port (127.0.0.1:9) mit kurzem Timeout aufgerufen: schnell, kein Werfen,
beruehrt nie das Internet.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import ai


def test_parse_tags():
    assert ai.parse_tags({"models": [{"name": "a"}, {"name": "b"}]}) == ["a", "b"]
    assert ai.parse_tags({}) == []
    assert ai.parse_tags({"models": []}) == []
    assert ai.parse_tags({"models": [{"size": 1}]}) == []  # kein name -> raus
    assert ai.parse_tags("garbage") == []


def test_parse_generate():
    assert ai.parse_generate({"response": "hi"}) == "hi"
    assert ai.parse_generate({}) is None
    assert ai.parse_generate({"response": ""}) is None
    assert ai.parse_generate("garbage") is None


def test_clean_output():
    assert ai.clean_output('  "Hello"  ') == "Hello"
    assert ai.clean_output("```\ntext\n```") == "text"
    assert ai.clean_output("```python\ncode\n```") == "code"
    assert ai.clean_output("a\n\n\n\n\nb") == "a\n\nb"
    assert ai.clean_output("plain") == "plain"
    assert ai.clean_output("") == ""
    assert ai.clean_output("   ") == ""
    assert ai.clean_output("“Smart”") == "Smart"            # typografisch
    # Nur ein VOLLSTAENDIG umschliessendes Paar wird entfernt:
    assert ai.clean_output('say "hi" now') == 'say "hi" now'


def test_build_generate_payload():
    p = ai.build_generate_payload("m", "p")
    assert p["model"] == "m"
    assert p["prompt"] == "p"
    assert p["stream"] is False
    assert p["options"] == {"temperature": 0.2}
    assert "system" not in p
    ps = ai.build_generate_payload("m", "p", system="s")
    assert ps["system"] == "s"
    assert ai.build_generate_payload("m", "p", system="")  # leer -> kein system
    assert "system" not in ai.build_generate_payload("m", "p", system="")
    po = ai.build_generate_payload("m", "p", options={"temperature": 0.9})
    assert po["options"] == {"temperature": 0.9}


def test_available_dead_port():
    # Toter Loopback-Port: schnelle Antwort, kein Werfen, nie Internet.
    assert ai.available("http://127.0.0.1:9", timeout=0.2) is False
    assert ai.list_models("http://127.0.0.1:9", timeout=0.2) == []


def test_generate_empty_model_no_network():
    # Leeres model schliesst sofort kurz, kein Netzwerk.
    assert ai.generate("p", model="") is None
    assert ai.generate("p", model=None) is None


# ------------------------------------------------------------ Proxy (A7)
import http.server
import json
import threading
import urllib.request

import pytest


class _FakeOllama:
    """Loopback-Server auf freiem Port mit /api/tags und /api/generate; zeichnet
    die Anfragen auf. Kein echtes Ollama, kein Netz."""

    def __init__(self):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append((self.command, self.path, None))
                self._reply({"models": [{"name": "m1"}]})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append((self.command, self.path, body))
                self._reply({"response": "ok"})

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.endpoint = "http://127.0.0.1:%d" % self.srv.server_port

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def fake_ollama():
    f = _FakeOllama()
    yield f
    f.close()


@pytest.fixture
def dead_system_proxy(monkeypatch):
    """Ein Systemproxy, der auf einen toten Port zeigt: alles, was ihn benutzt,
    scheitert. no_proxy leer, damit urllib 127.0.0.1 nicht selbst ausnimmt."""
    for var in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY",
                "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    # urlopen baut seinen Standard-Opener (und liest dabei die Proxy-Einstellungen)
    # nur einmal je Prozess. Ein frischer Prozess, wie die App, sieht den Proxy.
    monkeypatch.setattr(urllib.request, "_opener", None)


def test_loopback_calls_ignore_the_system_proxy(fake_ollama, dead_system_proxy):
    """Hinter einem Systemproxy ging der Diktattext an den Proxy (oder die
    Bereinigung scheiterte still). Auf Loopback darf kein Proxy im Weg sein."""
    assert ai.available(fake_ollama.endpoint, timeout=3) is True
    assert ai.list_models(fake_ollama.endpoint, timeout=3) == ["m1"]
    assert ai.generate("hallo", model="m1", endpoint=fake_ollama.endpoint,
                       timeout=3) == "ok"
    assert [r[1] for r in fake_ollama.requests] == \
        ["/api/tags", "/api/tags", "/api/generate"]


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:11434", "http://127.1.2.3:11434/", "http://localhost:11434",
    "http://LOCALHOST./", "http://[::1]:11434", "http://[::ffff:127.0.0.1]:11434",
    "http://ollama.localhost:11434",
])
def test_loopback_endpoints(endpoint):
    assert ai.is_loopback(endpoint) is True


@pytest.mark.parametrize("endpoint", [
    "", "127.0.0.1:11434", "http://192.168.1.5:11434", "https://ollama.example.com",
    "http://127.0.0.1@evil.example.com/", "http://localhost.evil.example.com",
    "http://0.0.0.0:11434", "http://[::2]:11434", "http://[",
])
def test_non_loopback_endpoints(endpoint):
    assert ai.is_loopback(endpoint) is False


def test_a_remote_endpoint_keeps_the_normal_opener(monkeypatch):
    """Ein Endpunkt im Netz darf weiter den Systemproxy nutzen (Firmennetz):
    nur Loopback wird vom Proxy ausgenommen."""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        raise OSError("kein Netz im Test")
    monkeypatch.setattr(ai.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ai._local_opener, "open",
                        lambda *a, **kw: pytest.fail("Proxy-freier Opener fuer Remote"))
    assert ai.available("http://192.0.2.7:11434", timeout=1) is False
    assert seen


# ------------------------------------------------------- Warnung (A8)
@pytest.mark.parametrize("endpoint,expected", [
    ("", None), ("   ", None),
    ("http://127.0.0.1:11434", None), ("https://localhost:11434", None),
    ("http://[::1]:11434", None),
    ("http://192.168.1.5:11434", "remote_plain"),
    ("http://ollama.example.com", "remote_plain"),
    ("https://ollama.example.com", "remote"),
    ("http://127.0.0.1@evil.example.com/", "remote_plain"),
    ("http://localhost.evil.example.com", "remote_plain"),
    ("127.0.0.1:11434", "invalid"), ("ftp://127.0.0.1", "invalid"),
    ("http://", "invalid"), ("http://[", "invalid"), ("nonsense", "invalid"),
])
def test_endpoint_warning(endpoint, expected):
    assert ai.endpoint_warning(endpoint) == expected


def test_endpoint_warning_accepts_the_shipped_default():
    assert ai.endpoint_warning(ai.OLLAMA_DEFAULT) is None
    assert ai.endpoint_warning(None) is None


# ----------------------------------------------------------- keep_alive (C51)
def test_payload_carries_keep_alive_only_when_given():
    assert "keep_alive" not in ai.build_generate_payload("m", "p")
    assert ai.build_generate_payload("m", "p", keep_alive="10m")["keep_alive"] == "10m"
    assert "keep_alive" not in ai.build_generate_payload("m", "p", keep_alive="")


def test_generate_keeps_the_model_loaded(fake_ollama):
    """Ollama entlaedt ein Modell nach fuenf Minuten; der naechste Aufruf zahlt
    dann den Kaltstart (20 bis 30 s). generate() bittet um mehr."""
    assert ai.generate("hallo", model="m1", endpoint=fake_ollama.endpoint,
                       timeout=3) == "ok"
    body = fake_ollama.requests[-1][2]
    assert body["keep_alive"] == ai.KEEP_ALIVE_DEFAULT == "30m"
    assert ai.generate("hallo", model="m1", endpoint=fake_ollama.endpoint,
                       timeout=3, keep_alive="2h") == "ok"
    assert fake_ollama.requests[-1][2]["keep_alive"] == "2h"


if __name__ == "__main__":
    for fn in [test_parse_tags, test_parse_generate, test_clean_output,
               test_build_generate_payload, test_available_dead_port,
               test_generate_empty_model_no_network]:
        fn()
    print("ok")
