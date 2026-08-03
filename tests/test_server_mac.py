"""Tests für quassel/server_mac.py — alles gemockt (kein echter Server)."""
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import config, server_mac


def test_build_args_full():
    """Prüft die Absicht von build_args(): ein beliebiger WHISPER_DECODE-Wert
    wird an Leerzeichen gesplittet 1:1 durchgereicht (nicht auf einen
    bestimmten Beam-Wert festverdrahtet) und die VAD-Flags kommen dazu, wenn
    die Datei existiert. "-bs 1" ist hier nur das reale Default-Beispiel."""
    env = {"SERVER_BIN": "/x/whisper-server", "MODEL_PATH": "/m/ggml-a.bin",
           "WHISPER_THREADS": "8", "WHISPER_DECODE": "-bs 1",
           "VAD_MODEL": __file__}          # existiert -> VAD-Flags dabei
    args = server_mac.build_args(env)
    assert args[:5] == ["/x/whisper-server", "-m", "/m/ggml-a.bin", "-t", "8"]
    assert "-bs" in args and "1" in args
    assert "--vad" in args and "--vad-model" in args and __file__ in args
    assert ["--host", "127.0.0.1", "--port", "8765", "-l", "auto", "-nt"] \
        == args[-7:]


def test_build_args_defaults_and_missing_vad():
    env = {"SERVER_BIN": "b", "MODEL_PATH": "m",
           "VAD_MODEL": "/nope/missing.bin"}
    args = server_mac.build_args(env)
    assert "-nf" in args              # Decode-Default
    assert "--vad" not in args        # VAD-Datei fehlt -> keine VAD-Flags


def test_server_bin_prefers_serverenv(tmp_path):
    fake = tmp_path / "whisper-server"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    with patch.object(config, "read_serverenv",
                      return_value={"SERVER_BIN": str(fake)}):
        assert server_mac.server_bin() == str(fake)


def test_server_bin_falls_back_to_vendor():
    with patch.object(config, "read_serverenv", return_value={}), \
         patch.object(os, "access", return_value=True):
        p = server_mac.server_bin()
    assert p.endswith(os.path.join("vendor", "whisper.cpp",
                                   "build", "bin", "whisper-server"))


def test_current_model_checks_existence(tmp_path):
    model = tmp_path / "ggml-x.bin"
    model.write_bytes(b"0" * 2048)
    with patch.object(config, "read_serverenv",
                      return_value={"MODEL_PATH": str(model)}):
        assert server_mac.current_model() == str(model)
    with patch.object(config, "read_serverenv",
                      return_value={"MODEL_PATH": "/nope.bin"}):
        assert server_mac.current_model() is None


def test_find_model_prefers_hwdetect_default(tmp_path):
    for name in ("small-q5_1", "large-v3-turbo-q5_0"):
        (tmp_path / f"ggml-{name}.bin").write_bytes(b"0" * 2048)
    with patch.object(server_mac, "MODEL_DIR", str(tmp_path)), \
         patch("quassel.hwdetect.default_model_for_hardware",
               return_value="large-v3-turbo-q5_0"):
        assert server_mac._find_model().endswith("ggml-large-v3-turbo-q5_0.bin")


def test_find_model_falls_back_to_present(tmp_path):
    (tmp_path / "ggml-small-q5_1.bin").write_bytes(b"0" * 2048)
    with patch.object(server_mac, "MODEL_DIR", str(tmp_path)), \
         patch("quassel.hwdetect.default_model_for_hardware",
               return_value="large-v3-turbo"):
        assert server_mac._find_model().endswith("ggml-small-q5_1.bin")


def test_ensure_env_fills_missing_only(tmp_path):
    binpath = tmp_path / "whisper-server"
    binpath.write_text("")
    binpath.chmod(0o755)
    model = tmp_path / "ggml-small-q5_1.bin"
    model.write_bytes(b"0" * 2048)
    written = {}
    with patch.object(config, "read_serverenv",
                      return_value={"WHISPER_DECODE": "-nf"}), \
         patch.object(config, "write_serverenv",
                      side_effect=lambda e: written.update(e)), \
         patch.object(server_mac, "server_bin", return_value=str(binpath)), \
         patch.object(server_mac, "_find_model", return_value=str(model)), \
         patch.object(server_mac, "current_model",
                      side_effect=[None, str(model)]), \
         patch.object(server_mac, "vad_model_path", return_value=None):
        assert server_mac.ensure_env() is True
    assert written["SERVER_BIN"] == str(binpath)
    assert written["MODEL_PATH"] == str(model)
    assert written["WHISPER_DECODE"] == "-nf"      # bestehende Wahl unangetastet
    assert written["WHISPER_THREADS"] == str(min(8, os.cpu_count() or 4))
    assert "VAD_MODEL" not in written


def test_ensure_env_defaults_to_greedy_decode(tmp_path):
    """Die eigentliche Absicht der Umstellung: fehlt WHISPER_DECODE, wählt
    ensure_env() gierige Suche ("-bs 1"), NICHT mehr Beam-Search ("-bs 5") —
    eigene Messung über 36 Dateien/938 Referenzwörter zeigte beam_size=5 nie
    besser, ~6% langsamer."""
    binpath = tmp_path / "whisper-server"
    binpath.write_text("")
    binpath.chmod(0o755)
    model = tmp_path / "ggml-small-q5_1.bin"
    model.write_bytes(b"0" * 2048)
    written = {}
    with patch.object(config, "read_serverenv", return_value={}), \
         patch.object(config, "write_serverenv",
                      side_effect=lambda e: written.update(e)), \
         patch.object(server_mac, "server_bin", return_value=str(binpath)), \
         patch.object(server_mac, "_find_model", return_value=str(model)), \
         patch.object(server_mac, "current_model",
                      side_effect=[None, str(model)]), \
         patch.object(server_mac, "vad_model_path", return_value=None):
        assert server_mac.ensure_env() is True
    assert written["WHISPER_DECODE"] == "-bs 1"
    assert written["WHISPER_DECODE"] != "-bs 5"


def _ensure_env_with_decode(decode_value, binpath, model):
    """Wie ensure_env(), aber mit SERVER_BIN/MODEL_PATH/THREADS schon gefüllt
    -- damit changed ausschließlich vom WHISPER_DECODE-Zweig herrührt und die
    Migrationslogik isoliert geprüft werden kann."""
    written = {}
    env = {"SERVER_BIN": str(binpath), "MODEL_PATH": str(model),
           "WHISPER_THREADS": "8", "VAD_MODEL": "x"}
    if decode_value is not None:
        env["WHISPER_DECODE"] = decode_value
    with patch.object(config, "read_serverenv", return_value=env), \
         patch.object(config, "write_serverenv",
                      side_effect=lambda e: written.update(e)), \
         patch.object(os, "access", return_value=True), \
         patch.object(server_mac, "current_model", return_value=str(model)), \
         patch.object(server_mac, "vad_model_path", return_value=None):
        assert server_mac.ensure_env() is True
    return written


def test_ensure_env_migrates_the_exact_old_beam_default(tmp_path):
    """Bestandsnutzer: server.env enthält noch den alten Vorgabewert dieses
    Programms selbst -- kein erkennbarer Nutzerwille, wird angehoben. Auf
    dieser Maschine nachweisbar: die laufende Instanz (PID 6506) hat exakt
    diesen alten Wert in ihrer server.env stehen (siehe Result-File)."""
    binpath = tmp_path / "whisper-server"
    binpath.write_text("")
    binpath.chmod(0o755)
    model = tmp_path / "ggml-small-q5_1.bin"
    model.write_bytes(b"0" * 2048)
    written = _ensure_env_with_decode("-bs 5", binpath, model)
    assert written["WHISPER_DECODE"] == "-bs 1"


def test_ensure_env_migrates_the_old_default_with_extra_whitespace(tmp_path):
    """Nach Normalisierung von Leerraum -- "-bs  5" oder "  -bs 5  " zählen
    genauso als der alte Vorgabewert wie "-bs 5"."""
    binpath = tmp_path / "whisper-server"
    binpath.write_text("")
    binpath.chmod(0o755)
    model = tmp_path / "ggml-small-q5_1.bin"
    model.write_bytes(b"0" * 2048)
    for messy in ("-bs  5", "  -bs 5  ", "-bs\t5"):
        assert _ensure_env_with_decode(messy, binpath, model)["WHISPER_DECODE"] == "-bs 1", messy


def test_ensure_env_preserves_a_deliberate_decode_value(tmp_path):
    """Alles außer EXAKT dem alten Vorgabewert ist eine bewusste Nutzerwahl
    und bleibt unangetastet -- eigener Wert, ein anderer Beam-Size, "-nf",
    oder "-bs 5" mit zusätzlichen Flags (kein exakter Treffer mehr)."""
    binpath = tmp_path / "whisper-server"
    binpath.write_text("")
    binpath.chmod(0o755)
    model = tmp_path / "ggml-small-q5_1.bin"
    model.write_bytes(b"0" * 2048)
    for own_value in ("-bs 8", "-nf", "-bs 5 -nf", "--my-custom-flag"):
        written = _ensure_env_with_decode(own_value, binpath, model)
        assert written.get("WHISPER_DECODE", own_value) == own_value, own_value


def test_ensure_env_migration_is_idempotent(tmp_path):
    """Nach der Anhebung steht "-bs 1" da -- ein zweiter Lauf darf write_serverenv
    für dieses Feld nicht nochmal auslösen (changed bleibt False)."""
    binpath = tmp_path / "whisper-server"
    binpath.write_text("")
    binpath.chmod(0o755)
    model = tmp_path / "ggml-small-q5_1.bin"
    model.write_bytes(b"0" * 2048)
    written = _ensure_env_with_decode("-bs 1", binpath, model)
    assert written == {}                    # nichts geändert -> write_serverenv nie gerufen


def test_start_spawns_server_and_is_idempotent(tmp_path):
    binpath = tmp_path / "whisper-server"
    binpath.write_text("")
    env = {"SERVER_BIN": str(binpath), "MODEL_PATH": "m",
           "WHISPER_THREADS": "4", "WHISPER_DECODE": "-bs 1"}
    proc = MagicMock()
    proc.poll.return_value = None
    server_mac._proc = None
    try:
        with patch.object(server_mac, "ensure_env", return_value=True), \
             patch.object(server_mac, "port_in_use", return_value=False), \
             patch.object(config, "read_serverenv", return_value=env), \
             patch.object(subprocess, "Popen", return_value=proc) as popen:
            assert server_mac.start() is True
            assert server_mac.start() is True     # läuft schon -> kein 2. Popen
        assert popen.call_count == 1
        assert popen.call_args.args[0][0] == str(binpath)
        # eigene Prozessgruppe -> Shutdown kann die ganze Gruppe beenden
        assert popen.call_args.kwargs["start_new_session"] is True
    finally:
        server_mac._proc = None


def test_start_cwd_guard_for_bare_binary_name():
    env = {"SERVER_BIN": "whisper-server", "MODEL_PATH": "m",
           "WHISPER_THREADS": "4", "WHISPER_DECODE": "-nf"}
    proc = MagicMock()
    proc.poll.return_value = None
    server_mac._proc = None
    try:
        with patch.object(server_mac, "ensure_env", return_value=True), \
             patch.object(server_mac, "port_in_use", return_value=False), \
             patch.object(config, "read_serverenv", return_value=env), \
             patch.object(subprocess, "Popen", return_value=proc) as popen:
            assert server_mac.start() is True
        # dirname("whisper-server") == "" wäre ein Popen-Crash -> None
        assert popen.call_args.kwargs["cwd"] is None
    finally:
        server_mac._proc = None


def test_start_skips_spawn_when_port_already_bound():
    server_mac._proc = None
    with patch.object(server_mac, "port_in_use", return_value=True), \
         patch.object(subprocess, "Popen") as popen:
        assert server_mac.start() is True
    popen.assert_not_called()


def test_start_reaps_dead_child():
    dead = MagicMock()
    dead.poll.return_value = 1
    server_mac._proc = dead
    try:
        with patch.object(server_mac, "port_in_use", return_value=False), \
             patch.object(server_mac, "ensure_env", return_value=False):
            assert server_mac.start() is False
        dead.wait.assert_called_once()            # Zombie geerntet
    finally:
        server_mac._proc = None


def test_start_fails_without_env():
    server_mac._proc = None
    with patch.object(server_mac, "port_in_use", return_value=False), \
         patch.object(server_mac, "ensure_env", return_value=False):
        assert server_mac.start() is False


def test_port_in_use_real_socket(monkeypatch):
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    monkeypatch.setattr(server_mac, "PORT", str(port))
    try:
        assert server_mac.port_in_use() is True
    finally:
        srv.close()
    assert server_mac.port_in_use() is False


def test_stop_terminates_child():
    proc = MagicMock()
    proc.poll.return_value = None
    server_mac._proc = proc
    with patch.object(server_mac, "terminate_group") as tg:
        server_mac.stop()
    tg.assert_called_once_with(proc)
    assert server_mac._proc is None


def test_stop_without_child_is_noop():
    """Kein pkill mehr auf dem sauberen Exit-Pfad (Review M1)."""
    server_mac._proc = None
    with patch.object(subprocess, "run") as run:
        server_mac.stop()
    run.assert_not_called()


def test_stop_reaps_already_dead_child():
    dead = MagicMock()
    dead.poll.return_value = 0
    server_mac._proc = dead
    server_mac.stop()
    dead.wait.assert_called_once()
    assert server_mac._proc is None


def test_terminate_group_kills_group_and_reaps(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    proc = MagicMock()
    proc.pid = 4242
    server_mac.terminate_group(proc)
    assert calls and calls[0][0] == 4242
    proc.wait.assert_called_once()


def test_terminate_group_falls_back_when_killpg_fails(monkeypatch):
    def boom(pgid, sig):
        raise OSError("keine Gruppe")
    monkeypatch.setattr(os, "killpg", boom)
    proc = MagicMock()
    proc.pid = 4242
    server_mac.terminate_group(proc)
    proc.terminate.assert_called_once()


def test_kill_orphans_matches_only_exact_bin_and_port(monkeypatch):
    binpath = "/repo/vendor/whisper.cpp/build/bin/whisper-server"
    argv_by_pid = {
        "111": binpath + " -m model.bin --host 127.0.0.1 --port 8765 -nt",
        "222": "tail -f whisper-server.log --port 8765",     # fremder Prozess
        "333": binpath + " -m model.bin --port 9999",        # anderer Port
    }

    def fake_run(args, **kw):
        if args[0] == "pgrep":
            return MagicMock(stdout="111\n222\n333\n")
        if args[0] == "ps":
            return MagicMock(stdout=argv_by_pid[args[-1]] + "\n")
        raise AssertionError(args)

    killed = []
    monkeypatch.setattr(server_mac, "server_bin", lambda: binpath)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))

    server_mac.kill_orphans()

    assert killed == [111]


def test_kill_orphans_noop_without_binary(monkeypatch):
    monkeypatch.setattr(server_mac, "server_bin", lambda: None)
    with patch.object(subprocess, "run") as run:
        server_mac.kill_orphans()
    run.assert_not_called()


def test_default_starter_uses_server_mac_on_darwin():
    from quassel import whisperclient
    with patch.object(whisperclient.sys, "platform", "darwin"), \
         patch.object(server_mac, "start") as start:
        whisperclient._default_starter()
    start.assert_called_once()
