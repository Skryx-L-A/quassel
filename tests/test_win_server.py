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


def test_start_never_reads_a_persisted_decode_value(tmp_path):
    """Belegt die Begründung im Docstring: server.env wird für den Decode-Wert
    gar nicht erst gelesen -- config.read_serverenv() darf in start() nicht
    aufgerufen werden. Deshalb betrifft die Migration in server_mac.ensure_env()
    (Bestandsnutzer mit altem "-bs 5" in server.env) diesen Pfad nicht."""
    exe = tmp_path / "whisper-server.exe"
    exe.write_text("")
    proc = MagicMock()
    server._proc = None

    def boom(*a, **kw):
        raise AssertionError("start() hat server.env gelesen")
    try:
        with patch.object(server, "server_exe", return_value=str(exe)), \
             patch.object(server, "current_model", return_value="m.bin"), \
             patch.object(server, "has_nvidia", return_value=True), \
             patch.object(server, "vad_model_path", return_value=None), \
             patch.object(server.config, "read_serverenv", side_effect=boom), \
             patch.object(subprocess, "Popen", return_value=proc):
            server.start()
    finally:
        server._proc = None
