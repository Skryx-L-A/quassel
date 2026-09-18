"""Das Wake-Word-Log darf nie erkannten Text enthalten (Audit A1): der Listener
transkribiert alles, was im Raum gesprochen wird, nicht nur Diktate."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import daemon as daemon_mod

SECRET = "mein Passwort ist Sonnenblume42"


def _run(monkeypatch, transcript, phrase="Hey Quassel"):
    logged = []
    monkeypatch.setattr(daemon_mod, "log", logged.append)
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server", lambda: True)
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                        lambda *a, **kw: transcript)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", lambda pcm, path: None)
    fake = SimpleNamespace(cfg=SimpleNamespace(wakeword_phrase=phrase))
    result = daemon_mod.Daemon._wake_transcribe(fake, b"\x00" * 100)
    return result, logged


def test_the_log_carries_no_recognised_text(monkeypatch):
    result, logged = _run(monkeypatch, SECRET)
    assert result == SECRET                     # der Rückgabewert bleibt unverändert
    assert logged                               # es wird weiterhin etwas geloggt
    for line in logged:
        assert "Sonnenblume42" not in line
        assert "Passwort" not in line


def test_the_log_carries_length_and_match(monkeypatch):
    _, logged = _run(monkeypatch, "Hey Quassel, schreib das auf")
    line = " ".join(logged)
    assert "28 chars" in line
    assert "match=yes" in line
    _, logged = _run(monkeypatch, SECRET)
    line = " ".join(logged)
    assert "%d chars" % len(SECRET) in line
    assert "match=no" in line


def test_the_log_does_not_choke_on_no_transcript(monkeypatch):
    result, logged = _run(monkeypatch, None)
    assert result is None
    assert "0 chars" in " ".join(logged)
