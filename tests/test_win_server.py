"""Tests für quassel/win/server.py.

Anders als win/app.py und win/paste.py (ctypes.windll beim Import) hängt
win/server.py an keiner Windows-only-API — importierbar wie jedes andere
Modul, nur Popen/has_nvidia werden gemockt. Deckt insbesondere die
Absicht der Umstellung auf gierige Suche ab (server_mac.py-Pendant): der
Decode-Vorgabewert wird bei JEDEM start() frisch aus has_nvidia() gebaut,
nie aus einer persistierten server.env gelesen -- deshalb ist hier (anders
als bei server_mac.ensure_env()) keine Migration für Bestandsnutzer nötig."""
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel.win import server
import pytest


@pytest.fixture(autouse=True)
def isolated_server_files(tmp_path, monkeypatch):
    monkeypatch.setattr(server.config, "DATADIR", str(tmp_path))
    monkeypatch.setattr(server, "PID_FILE", str(tmp_path / "server.pid.json"))
    monkeypatch.setattr(server, "LOG_FILE", str(tmp_path / "server.log"))


def test_start_uses_greedy_decode_with_nvidia(tmp_path):
    """Eigene Messung über 36 Dateien/938 Referenzwörter: beam_size=5 in
    keiner Datei besser, ~6% langsamer -- gilt auch für den NVIDIA-Zweig,
    der früher Beam-Search bekam."""
    exe = tmp_path / "whisper-server.exe"
    exe.write_text("")
    proc = MagicMock()
    server._proc = None
    try:
        with patch.object(server, "server_exe", return_value=str(exe)), \
             patch.object(server, "current_model", return_value="m.bin"), \
             patch.object(server, "has_nvidia", return_value=True), \
             patch.object(server, "vad_model_path", return_value=None), \
             patch.object(subprocess, "Popen", return_value=proc) as popen:
            server.start()
        args = popen.call_args.args[0]
        assert not {"-pr", "--print-realtime", "-pp", "--print-progress"} & set(args)
        assert "-bs" in args
        assert args[args.index("-bs") + 1] == "1"
        assert "5" not in args      # der alte Vorgabewert darf nirgends mehr auftauchen
    finally:
        server._proc = None


def test_start_uses_no_fallback_flag_without_nvidia(tmp_path):
    exe = tmp_path / "whisper-server.exe"
    exe.write_text("")
    proc = MagicMock()
    server._proc = None
    try:
        with patch.object(server, "server_exe", return_value=str(exe)), \
             patch.object(server, "current_model", return_value="m.bin"), \
             patch.object(server, "has_nvidia", return_value=False), \
             patch.object(server, "vad_model_path", return_value=None), \
             patch.object(subprocess, "Popen", return_value=proc) as popen:
            server.start()
        args = popen.call_args.args[0]
        assert "-nf" in args
        assert "-bs" not in args    # ohne NVIDIA gibt es keinen Beam-Zweig mehr zu wählen
    finally:
        server._proc = None


def test_start_reads_only_the_request_path_from_serverenv(tmp_path):
    exe = tmp_path / "whisper-server.exe"
    exe.write_text("")
    proc = MagicMock()
    server._proc = None

    env = {"REQUEST_PATH": "/q/already-private"}
    try:
        with patch.object(server, "server_exe", return_value=str(exe)), \
             patch.object(server, "current_model", return_value="m.bin"), \
             patch.object(server, "has_nvidia", return_value=True), \
             patch.object(server, "vad_model_path", return_value=None), \
             patch.object(server.config, "read_serverenv", return_value=env), \
             patch.object(server, "_supports_request_path", return_value=True), \
             patch.object(subprocess, "Popen", return_value=proc) as popen:
            server.start()
        assert popen.call_args.args[0][-2:] == ["--request-path", "/q/already-private"]
    finally:
        server._proc = None


def test_stop_never_uses_image_wide_kill(tmp_path):
    server._proc = None
    server._write_pid(1234, str(tmp_path / "whisper-server.exe"), 19001)
    with patch.object(server, "_owned_orphan", return_value=True), \
         patch.object(subprocess, "run") as run:
        server.stop()
    assert run.call_args.args[0] == ["taskkill", "/F", "/PID", "1234"]
    assert not os.path.exists(server.PID_FILE)


def test_unverified_pid_is_not_killed(tmp_path):
    server._proc = None
    server._write_pid(1234, str(tmp_path / "whisper-server.exe"), 19001)
    with patch.object(server, "_owned_orphan", return_value=False), \
         patch.object(subprocess, "run") as run:
        server.stop()
    run.assert_not_called()


def test_log_rotates_to_two_generations(tmp_path):
    with open(server.LOG_FILE, "wb") as out:
        out.write(b"x" * (1024 * 1024))
    with server._log_stream() as out:
        out.write(b"new")
    assert open(server.LOG_FILE, "rb").read() == b"new"
    assert os.path.getsize(server.LOG_FILE + ".1") == 1024 * 1024
