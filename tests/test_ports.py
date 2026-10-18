"""Portwahl des whisper-servers (quassel/ports.py) und ihre Weitergabe.

Kein Test bindet oder probt 8765 und keiner startet einen echten
whisper-server: Vorgabe- und Ausweichports sind zufällige freie Ports,
"unser Server" ist ein kleiner HTTP-Server mit dem Header Server: whisper.cpp.
"""
import http.server
import os
import shlex
import socket
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from quassel import config, diagnostics, ports, selfheal, server_mac, whisperclient

ROOT = Path(__file__).resolve().parents[1]
_REAL_FALLBACK = list(ports.FALLBACK)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Ohne Laufzeitdatei, ohne QUASSEL_PORT und nie mit dem echten 8765."""
    monkeypatch.delenv("QUASSEL_PORT", raising=False)
    monkeypatch.delenv("QUASSEL_PROFILE", raising=False)
    monkeypatch.setattr(config, "read_serverenv", lambda: {})
    monkeypatch.setattr(ports, "default", lambda: pytest.fail("Vorgabeport nicht gesetzt"))
    monkeypatch.setattr(ports, "FALLBACK", [])
    yield
    try:
        os.remove(ports.path())
    except OSError:
        pass


@pytest.fixture
def foreign_listener():
    """Fremder Dienst: nimmt Verbindungen an und schließt sie sofort, ohne HTTP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)

    def serve():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    yield sock.getsockname()[1]
    sock.close()


def _http_server(server_header):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def version_string(self):
            return server_header

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def own_server():
    srv = _http_server("whisper.cpp")
    yield srv.server_port
    srv.shutdown()
    srv.server_close()


def _use_default(monkeypatch, port, fallback=()):
    monkeypatch.setattr(ports, "default", lambda: port)
    monkeypatch.setattr(ports, "FALLBACK", list(fallback))


def _write(text):
    os.makedirs(os.path.dirname(ports.path()), exist_ok=True)
    with open(ports.path(), "w", encoding="utf-8") as f:
        f.write(text)


# --------------------------------------------------------------- Laufzeitdatei

@pytest.mark.parametrize("content", [None, "", "abc", "80", "99999", "12 34", "²³"])
def test_missing_or_broken_runtime_file_falls_back_to_default(monkeypatch, content):
    _use_default(monkeypatch, 40123)
    if content is not None:
        _write(content)
    assert ports.read_file() is None
    assert ports.current() == 40123
    assert whisperclient.server_base() == "http://127.0.0.1:40123"


def test_runtime_file_is_read_by_every_client(monkeypatch):
    _use_default(monkeypatch, 40123)
    _write("40999\n")
    assert ports.current() == 40999
    assert whisperclient.server_url().startswith("http://127.0.0.1:40999")
    assert "port: 40999" in diagnostics.part_port()
    assert any("default port 40123" in line for line in diagnostics.part_port())


def test_fixed_port_ignores_a_stale_runtime_file(monkeypatch):
    port = _free_port()
    monkeypatch.setenv("QUASSEL_PORT", str(port))
    monkeypatch.setattr(ports, "default", lambda: port)
    _write("40999\n")
    assert ports.current() == port


# --------------------------------------------------------------------- Auswahl

def test_free_default_port_is_used_and_recorded(monkeypatch):
    port = _free_port()
    _use_default(monkeypatch, port)
    choice = ports.choose()
    assert (choice.port, choice.status) == (port, ports.FREE)
    assert ports.read_file() == port


def test_foreign_listener_makes_quassel_switch_and_clients_follow(
        monkeypatch, foreign_listener, capsys):
    other = _free_port()
    _use_default(monkeypatch, foreign_listener, fallback=[foreign_listener, other])
    assert ports.status(foreign_listener) == ports.BUSY
    choice = ports.choose()
    assert (choice.port, choice.status) == (other, ports.FREE)
    assert ports.read_file() == other
    assert ports.current() == other
    assert whisperclient.server_base() == "http://127.0.0.1:%d" % other
    assert "port: %d" % other in diagnostics.part_port()
    err = capsys.readouterr().err
    assert err.count("is in use by another program") == 1
    # Ein zweiter Start meldet den Wechsel nicht noch einmal.
    assert ports.choose().port == other
    assert "in use" not in capsys.readouterr().err


def test_selfheal_probes_the_chosen_port(monkeypatch):
    _use_default(monkeypatch, 40123)
    _write("40999\n")
    monkeypatch.setattr(whisperclient, "server_up", lambda timeout=2: True)
    seen = []

    class Conn:
        def __init__(self, host, port, timeout=None):
            seen.append((host, port))

        def request(self, *a, **k):
            raise OSError("kein echter Server")

        def close(self):
            pass

    monkeypatch.setattr(selfheal.http.client, "HTTPConnection", Conn)
    assert selfheal.functional_probe(timeout=1) is False
    assert seen == [("127.0.0.1", 40999)]


def test_own_server_on_the_default_port_is_reused(monkeypatch, own_server):
    _use_default(monkeypatch, own_server, fallback=[_free_port()])
    choice = ports.choose()
    assert (choice.port, choice.status) == (own_server, ports.OURS)
    assert ports.current() == own_server


def test_foreign_http_service_is_not_taken_for_ours(monkeypatch):
    srv = _http_server("OtherService/1.0")
    try:
        _use_default(monkeypatch, srv.server_port)
        assert ports.status(srv.server_port) == ports.BUSY
    finally:
        srv.shutdown()
        srv.server_close()


def test_own_server_on_the_previously_chosen_port_is_reused(
        monkeypatch, foreign_listener, own_server):
    _use_default(monkeypatch, foreign_listener, fallback=[_free_port()])
    _write("%d\n" % own_server)
    choice = ports.choose()
    assert (choice.port, choice.status) == (own_server, ports.OURS)


def test_fixed_quassel_port_in_use_reports_and_does_not_switch(
        monkeypatch, foreign_listener, capsys):
    monkeypatch.setenv("QUASSEL_PORT", str(foreign_listener))
    _use_default(monkeypatch, foreign_listener, fallback=[_free_port()])
    choice = ports.choose()
    assert (choice.port, choice.status) == (foreign_listener, ports.BUSY)
    assert "in use by another program" in choice.message
    assert "QUASSEL_PORT" in choice.message
    assert ports.current() == foreign_listener
    assert "QUASSEL_PORT is set" in capsys.readouterr().err


def test_no_free_port_left_is_reported(monkeypatch, foreign_listener):
    _use_default(monkeypatch, foreign_listener, fallback=[foreign_listener])
    choice = ports.choose()
    assert choice.status == ports.BUSY
    assert "all in use" in choice.message


def test_main_prints_the_port_for_the_systemd_unit(monkeypatch, capsys):
    port = _free_port()
    _use_default(monkeypatch, port)
    assert ports.main() == 0
    assert capsys.readouterr().out.strip() == str(port)


def test_fallback_range_avoids_the_default_and_profile_ports():
    assert _REAL_FALLBACK
    assert all(not 8765 <= p <= 8865 for p in _REAL_FALLBACK)


# --------------------------------------------------------------- macOS-Server

def _env(tmp_path):
    binpath = tmp_path / "whisper-server"
    binpath.write_text("")
    return {"SERVER_BIN": str(binpath), "MODEL_PATH": "m",
            "WHISPER_THREADS": "4", "WHISPER_DECODE": "-bs 1"}


def test_server_mac_starts_on_the_chosen_port(monkeypatch, tmp_path, foreign_listener):
    other = _free_port()
    _use_default(monkeypatch, foreign_listener, fallback=[other])
    env = _env(tmp_path)
    proc = MagicMock()
    proc.poll.return_value = None
    server_mac._proc = None
    try:
        with patch.object(server_mac, "ensure_env", return_value=True), \
             patch.object(server_mac.config, "read_serverenv", return_value=env), \
             patch.object(subprocess, "Popen", return_value=proc) as popen:
            assert server_mac.start() is True
        args = popen.call_args.args[0]
        assert args[args.index("--port") + 1] == str(other)
    finally:
        server_mac._proc = None
        for name in os.listdir(os.path.dirname(ports.path())):
            if name.startswith("whisper-server."):
                os.remove(os.path.join(os.path.dirname(ports.path()), name))


def test_server_mac_does_not_start_a_second_own_server(monkeypatch, own_server):
    _use_default(monkeypatch, own_server)
    server_mac._proc = None
    with patch.object(subprocess, "Popen") as popen:
        assert server_mac.start() is True
    popen.assert_not_called()


def test_server_mac_refuses_a_fixed_busy_port(monkeypatch, foreign_listener):
    monkeypatch.setenv("QUASSEL_PORT", str(foreign_listener))
    _use_default(monkeypatch, foreign_listener, fallback=[_free_port()])
    server_mac._proc = None
    with patch.object(subprocess, "Popen") as popen:
        assert server_mac.start() is False
    popen.assert_not_called()


def test_server_mac_orphan_check_knows_the_chosen_port(monkeypatch):
    _use_default(monkeypatch, 40123)
    _write("40999\n")
    binpath = "/x/whisper-server"
    for argv, own in ((binpath + " -m m --port 40999", True),
                      (binpath + " -m m --port 40123", True),
                      (binpath + " -m m --port 40998", False)):
        monkeypatch.setattr(subprocess, "run", lambda *a, _o=argv, **k:
                            MagicMock(stdout=_o + "\n"))
        assert server_mac._is_own_server(4242, binpath) is own


# ---------------------------------------------------------------- systemd-Units

def _unit_exec(name, runtime, instance=""):
    """ExecStart einer Unit so, wie systemd sie an /bin/sh gibt."""
    unit = (ROOT / "systemd" / name).read_text(encoding="utf-8")
    line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    line = (line[len("ExecStart="):].replace("%i", instance)
            .replace("%t", str(runtime)).replace("%%", "%"))
    return shlex.split(line)


def _run_unit(argv, **extra):
    env = {"PATH": os.environ["PATH"], "SERVER_BIN": "echo", "MODEL_PATH": "/m.bin", **extra}
    out = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20).stdout.split()
    return out[out.index("--port") + 1]


@pytest.mark.skipif(os.name == "nt", reason="startet die systemd-ExecStart-Zeile über /bin/sh")
def test_default_unit_reads_the_chosen_port(tmp_path):
    argv = _unit_exec("quassel-server.service", tmp_path)
    assert _run_unit(argv) == "8765"                       # keine Datei: wie bisher
    assert _run_unit(argv, QUASSEL_PORT="9123") == "9123"
    (tmp_path / "quassel").mkdir()
    (tmp_path / "quassel" / "server.port").write_text("kaputt\n")
    assert _run_unit(argv) == "8765"
    (tmp_path / "quassel" / "server.port").write_text("8871\n")
    assert _run_unit(argv) == "8871"


@pytest.mark.skipif(os.name == "nt", reason="startet die systemd-ExecStart-Zeile über /bin/sh")
def test_profile_unit_reads_the_chosen_port(tmp_path):
    from quassel import profile
    argv = _unit_exec("quassel-server@.service", tmp_path, "next")
    with patch.dict(os.environ, {"QUASSEL_PROFILE": "next"}):
        os.environ.pop("QUASSEL_PORT", None)
        expected = str(profile.port())
    assert _run_unit(argv, QUASSEL_PROFILE="next") == expected
    (tmp_path / "quassel-next").mkdir()
    (tmp_path / "quassel-next" / "server.port").write_text("8872\n")
    assert _run_unit(argv, QUASSEL_PROFILE="next") == "8872"


def test_units_choose_the_port_before_start_without_root():
    for name, rundir in (("quassel-server.service", "%t/quassel"),
                         ("quassel-server@.service", "%t/quassel-%i")):
        unit = (ROOT / "systemd" / name).read_text(encoding="utf-8")
        assert "ExecStartPre=-%h/.local/bin/quassel-server-port" in unit
        assert "-" + rundir in next(l for l in unit.splitlines()
                                    if l.startswith("ReadWritePaths="))
    launcher = (ROOT / "bin" / "quassel-server-port").read_text(encoding="utf-8")
    assert "quassel-python" in launcher and "-m quassel.ports" in launcher
    online = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert '"$SRC/bin/quassel-server-port"' in online
    offline = (ROOT / "scripts/offline/bundle-files/install.sh").read_text(encoding="utf-8")
    assert "ExecStartPre=-$BIN/quassel-server-port" in offline
    assert "-m quassel.ports" in offline
    assert "--port 8765" not in offline
