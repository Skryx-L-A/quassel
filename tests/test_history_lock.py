"""Verlauf: ein Leser, der abgelaufene Einträge entfernt, verliert kein Diktat,
das der Daemon gleichzeitig anhängt (Review der Nacht 2026-10-09, Punkt 5)."""
import json
import time

from quassel import config


def test_pruning_reader_keeps_a_dictation_appended_meanwhile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY", str(tmp_path / "history.jsonl"))
    old = time.time() - 30 * 86400
    (tmp_path / "history.jsonl").write_text(
        json.dumps({"text": "alt", "ts": old}) + "\n", encoding="utf-8")
    real = config._read_history_file
    calls = {"n": 0}

    def read_then_daemon_appends(cutoff):
        out = real(cutoff)
        calls["n"] += 1
        if calls["n"] == 1:
            # Der Daemon hängt an, nachdem der Leser gelesen, aber bevor er geschrieben hat.
            monkeypatch.setattr(config, "_read_history_file", real)
            config.history_append("neues Diktat", history_days=7)
            monkeypatch.setattr(config, "_read_history_file", read_then_daemon_appends)
        return out

    monkeypatch.setattr(config, "_read_history_file", read_then_daemon_appends)
    config.history_read(history_days=7)
    monkeypatch.setattr(config, "_read_history_file", real)
    texts = [e["text"] for e in config.history_read(history_days=7)]
    assert texts == ["neues Diktat"]


def test_history_file_stays_private(tmp_path, monkeypatch):
    import os
    import stat
    monkeypatch.setattr(config, "HISTORY", str(tmp_path / "history.jsonl"))
    config.history_append("x", history_days=7)
    assert stat.S_IMODE(os.stat(config.HISTORY).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(config.HISTORY + ".lock").st_mode) == 0o600
