"""Zweite Stufe der Bereinigung: ein lokaler typisierter Entscheider (Jev-Format).

Messung 09.10.: Die Wortprüfung ließ
auf Stufe "stark" 11 von 20 Bedeutungsänderungen durch (Rollentausch, gestrichene
Verneinung, "morgen" -> "übermorgen"). Ein Entscheider, der nur gefragt wird, wenn
die Regeln annehmen, erkannte alle 40 Paare richtig.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from quassel import ai, cleanup, config, decider


class _Server:
    def __init__(self, reply, status=200):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((self.path, json.loads(body)))
                data = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _answer(choice, p):
    rest = (1 - p) / 2
    probs = {"ja": rest, "nein": rest, "unklar": rest}
    probs[choice] = p
    return {"model": "lokal", "answers": {"q": {"type": "choice", "choice": choice,
                                                "probabilities": probs, "confidence": 0.9}}}


@pytest.fixture
def server():
    made = []

    def make(reply, status=200):
        made.append(_Server(reply, status))
        return made[-1]
    yield make
    for s in made:
        s.close()


def test_sends_one_typed_question_in_the_jev_wire_format(server):
    s = server(_answer("ja", 0.97))
    assert decider.same_meaning(s.url, "Ähm, ich komme morgen.", "Ich komme morgen.") is True
    [(path, body)] = s.requests
    assert path == "/v1/systemone"
    assert body["state"] == {"raw_dictation": "Ähm, ich komme morgen.",
                             "cleaned_text": "Ich komme morgen."}
    question = body["questions"]["q"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {"ja", "nein", "unklar"}


@pytest.mark.parametrize("choice,p,expected", [
    ("nein", 0.9, False), ("unklar", 0.8, False), ("ja", 0.55, False), ("ja", 0.8, False),
    ("ja", 0.9, True)])
def test_only_a_confident_yes_keeps_the_cleaned_text(server, choice, p, expected):
    s = server(_answer(choice, p))
    assert decider.same_meaning(s.url, "a", "b") is expected


@pytest.mark.parametrize("reply,status", [
    (b"kaputt", 200), ({"answers": {}}, 200), ({"error": "x"}, 422),
    ({"answers": {"q": {"type": "choice", "choice": "ja", "probabilities": "x"}}}, 200)])
def test_broken_answers_count_as_unavailable(server, reply, status):
    s = server(reply, status)
    assert decider.same_meaning(s.url, "a", "b") is None


def test_never_sends_text_off_the_machine(monkeypatch):
    sent = []
    monkeypatch.setattr(ai, "_open", lambda *a, **k: sent.append(a))
    for url in ("https://api.typesafe.ai", "http://192.168.1.5:9000", "ftp://127.0.0.1", ""):
        assert decider.same_meaning(url, "a", "b") is None
    assert sent == []


def test_unreachable_decider_is_unavailable():
    assert decider.same_meaning("http://127.0.0.1:9", "a", "b", timeout=0.5) is None


# ---------------------------------------------------------------- Bereinigung

def _cfg(url=""):
    return SimpleNamespace(cleanup_enabled=True, cleanup_replacements={}, ai_model="m",
                           ai_endpoint="http://127.0.0.1:11434", ai_timeout=5.0,
                           cleanup_terms=[], cleanup_decider_url=url,
                           cleanup_decider_timeout=2.0)


@pytest.fixture
def swapped(monkeypatch):
    """Das Modell setzt das Gegenwort ein; die Wortprüfung sieht das nicht (H025)."""
    monkeypatch.setattr(ai, "chat_cleanup",
                        lambda pre, **kw: ("Der Kunde hat die Lieferung angenommen.", None))
    return "Der Kunde hat die Lieferung abgelehnt."


def test_without_a_decider_the_rules_alone_decide(swapped, monkeypatch):
    monkeypatch.setattr(decider, "same_meaning", lambda *a, **k: pytest.fail("asked"))
    result = cleanup.cleanup(swapped, "stark", _cfg())
    assert result.text == "Der Kunde hat die Lieferung angenommen."   # heute: durchgelassen


def test_decider_no_keeps_the_raw_text(swapped, server):
    s = server(_answer("nein", 0.95))
    result = cleanup.cleanup(swapped, "stark", _cfg(s.url))
    assert result.text == swapped and result.fallback_reason == "decider_meaning"


def test_unavailable_decider_fails_closed(swapped):
    result = cleanup.cleanup(swapped, "stark", _cfg("http://127.0.0.1:9"))
    assert result.text == swapped and result.fallback_reason == "decider_unavailable"


def test_decider_yes_keeps_the_cleaned_text(monkeypatch, server):
    monkeypatch.setattr(ai, "chat_cleanup", lambda pre, **kw: ("Ich glaube, das ist gut.", None))
    s = server(_answer("ja", 0.97))
    result = cleanup.cleanup("ich glaube also das ist gut", "stark", _cfg(s.url))
    assert result.text == "Ich glaube, das ist gut." and result.fallback_reason is None
    assert len(s.requests) == 1


def test_decider_is_not_asked_when_the_rules_already_reject(monkeypatch, server):
    monkeypatch.setattr(ai, "chat_cleanup", lambda pre, **kw: ("Etwas ganz anderes.", None))
    s = server(_answer("ja", 0.97))
    result = cleanup.cleanup("Ich komme morgen vorbei und bringe Kuchen mit.", "stark", _cfg(s.url))
    assert result.fallback_reason not in (None, "decider_meaning", "decider_unavailable")
    assert s.requests == []


# ---------------------------------------------------------------- Einstellung

def test_config_reads_only_a_loopback_decider(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    (tmp_path / "config.ini").write_text(
        "[cleanup]\ndecider_url = http://127.0.0.1:8790\ndecider_timeout = 0.8\n")
    cfg = config.Cfg()
    assert cfg.cleanup_decider_url == "http://127.0.0.1:8790"
    assert cfg.cleanup_decider_timeout == 0.8
    (tmp_path / "config.ini").write_text("[cleanup]\ndecider_url = https://api.typesafe.ai\n")
    cfg = config.Cfg()
    assert cfg.cleanup_decider_url == ""


def test_decider_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    assert config.Cfg().cleanup_decider_url == ""


# ---------------------------------------------------------------- 09.10., Decider-4B

def test_accepts_the_full_service_address_too(server):
    s = server(_answer("ja", 0.97))
    assert decider.same_meaning(s.url + "/v1/systemone", "a", "b") is True
    assert s.requests[0][0] == "/v1/systemone"


def test_threshold_was_chosen_on_the_dev_pairs():
    """Decider-4B (jev-kandidaten, 09.10.): auf den dev-Paaren lag das
    unsichere Paar mit der höchsten Ja-Wahrscheinlichkeit bei 0,799
    ("might" -> "will"), das schwächste sichere bei 0,907. 0,85 trennt beide;
    auf den test-Paaren fällt ein sicheres Paar (0,78) auf den Rohtext zurück,
    kein unsicheres kommt durch."""
    assert 0.7986 < decider.MIN_YES <= 0.9065


@pytest.mark.parametrize("edited", ["ich komme morgen", "Ich komme morgen."])
def test_only_punctuation_or_case_changes_need_no_decider(monkeypatch, server, edited):
    monkeypatch.setattr(ai, "chat_cleanup", lambda pre, **kw: (edited, None))
    s = server(_answer("nein", 0.99))
    result = cleanup.cleanup("ich komme morgen", "stark", _cfg(s.url))
    assert result.fallback_reason is None and s.requests == []
