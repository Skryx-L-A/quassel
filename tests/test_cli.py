"""Offline coverage for the Quassel command-line interface."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import cli, config


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "DATADIR", str(tmp_path))
    monkeypatch.setattr(config, "DICTIONARY", str(tmp_path / "dictionary.txt"))
    monkeypatch.setattr(config, "HISTORY", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(config, "SERVERENV", str(tmp_path / "server.env"))
    monkeypatch.setattr(config, "MODELDIR", str(tmp_path / "models"))


def test_dictionary_commands_use_atomic_config_helpers(capsys):
    assert cli.main(["dict", "add", "Quassel"]) == 0
    assert config.dictionary_words() == ["Quassel"]
    capsys.readouterr()
    assert cli.main(["dict", "list"]) == 0
    assert capsys.readouterr().out == "Quassel\n"
    assert cli.main(["dict", "remove", "Quassel"]) == 0
    assert config.dictionary_words() == []


def test_model_use_never_downloads_without_the_explicit_flag(tmp_path, monkeypatch, capsys):
    downloaded = []
    monkeypatch.setattr(cli, "_download_model", lambda *args: downloaded.append(args) or True)
    assert cli.main(["model", "use", "tiny"]) == cli.EXIT_UNAVAILABLE
    assert downloaded == []
    assert "--download" in capsys.readouterr().err


def test_model_download_is_opt_in_and_writes_the_existing_server_config(tmp_path, monkeypatch):
    downloaded = []
    monkeypatch.setattr(cli, "_download_model", lambda name, target: downloaded.append((name, target)) or True)
    assert cli.main(["model", "use", "tiny", "--download"]) == 0
    assert downloaded and downloaded[0][0] == "tiny"
    assert config.read_serverenv()["MODEL_PATH"].endswith("ggml-tiny.bin")


def test_transcribe_json_warns_when_the_current_backend_has_no_timestamps(monkeypatch, tmp_path, capsys):
    source = tmp_path / "sample.wav"
    source.write_bytes(b"not read by mocked backend")
    from quassel.transcribe_file import FileTranscript
    monkeypatch.setattr("quassel.transcribe_file.transcribe_file",
                        lambda *a, **kw: FileTranscript("hello", ()))
    assert cli.main(["transcribe", str(source), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["text"] == "hello"
    assert result["segments"] == []
    assert "timestamps" in result["warning"]


def test_history_is_refused_when_the_privacy_switch_is_off(capsys):
    config.save({("history", "enabled"): "false"})
    config.history_append("private")
    assert cli.main(["history", "list"]) == cli.EXIT_UNAVAILABLE
    assert "disabled" in capsys.readouterr().err


def test_status_reports_profile_server_model_and_state(monkeypatch, capsys):
    config.write_serverenv({"MODEL_PATH": "/models/ggml-tiny.bin"})
    monkeypatch.setattr(cli.whisperclient, "server_up", lambda: True)
    monkeypatch.setattr(cli, "_daemon_status", lambda: "running")
    monkeypatch.setattr(cli.state, "state_read", lambda: {"state": "idle"})
    assert cli.main(["status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["server"] == "running"
    assert result["daemon"] == "running"
    assert result["model"] == "tiny"
