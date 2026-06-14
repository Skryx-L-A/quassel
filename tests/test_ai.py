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


if __name__ == "__main__":
    for fn in [test_parse_tags, test_parse_generate, test_clean_output,
               test_build_generate_payload, test_available_dead_port,
               test_generate_empty_model_no_network]:
        fn()
    print("ok")
