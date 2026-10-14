"""Offline regression checks for platform event wiring."""
import ctypes
import json
import os
import threading
from types import SimpleNamespace

import pytest

from quassel import daemon, power


def test_linux_reset_forgets_pressed_keys():
    d = daemon.Daemon.__new__(daemon.Daemon)
    d._linux_reset = threading.Event()
    pressed = daemon.PressedKeys()
    pressed.press(12, 29)
    assert not d._consume_linux_reset(pressed)
    d._linux_reset.set()
    assert d._consume_linux_reset(pressed)
    assert not pressed


def test_windows_native_filter_routes_power_message(monkeypatch):
    import test_win_stubs
    app = test_win_stubs.load_win_app(monkeypatch)
    from ctypes import wintypes
    events = []
    filt = app._PowerMessageFilter(power.PowerEvents(
        lambda: events.append("sleep"), lambda: events.append("wake")))
    msg = wintypes.MSG()
    msg.message = power.WM_POWERBROADCAST
    msg.wParam = power.PBT_APMSUSPEND
    assert filt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)
    msg.wParam = power.PBT_APMRESUMEAUTOMATIC
    filt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
    assert events == ["sleep", "wake"]


def test_linux_restart_requires_matching_profile_service(monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "_wait_functional_server", lambda: True)
    monkeypatch.setattr(daemon.profile, "name", lambda: "next")

    def run(args, **_kw):
        calls.append(args)
        if "show" in args:
            return SimpleNamespace(returncode=0,
                                   stdout="ActiveState=active\nEnvironment=QUASSEL_PROFILE=next\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(daemon.subprocess, "run", run)
    assert daemon._restart_linux_server()
    assert calls[-1][-1] == "quassel-server@next.service"
    calls.clear()
    monkeypatch.setattr(daemon.profile, "name", lambda: "other")
    assert not daemon._restart_linux_server()
    assert len(calls) == 1  # mismatched profile: no restart


def test_mac_app_handles_only_own_daemon_request(tmp_path, monkeypatch):
    from quassel import mac_app as entry, server_mac, state

    monkeypatch.setattr(state, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(server_mac, "_proc", SimpleNamespace(poll=lambda: None))
    events = []
    monkeypatch.setattr(server_mac, "stop", lambda: events.append("stop"))
    monkeypatch.setattr(server_mac, "start", lambda: events.append("start") or True)
    child = SimpleNamespace(pid=4321, poll=lambda: None)
    app = SimpleNamespace(enabled=True, daemon=child)
    request = {"token": "probe", "daemon_pid": child.pid, "app_pid": os.getpid(),
               "profile": daemon.profile.name(), "created": daemon.time.time()}
    (tmp_path / "server-restart.request").write_text(json.dumps(request))
    entry.handle_server_restart(app)
    assert events == ["stop", "start"]
    assert json.loads((tmp_path / "server-restart.ack").read_text()) == {
        "token": "probe", "started": True}
    request["daemon_pid"] = 9999
    (tmp_path / "server-restart.request").write_text(json.dumps(request))
    entry.handle_server_restart(app)
    assert events == ["stop", "start"]


def test_mac_daemon_request_requires_matching_ack(tmp_path, monkeypatch):
    request_path = tmp_path / "server-restart.request"
    ack_path = tmp_path / "server-restart.ack"
    monkeypatch.setattr(daemon, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(daemon, "SERVER_RESTART_REQUEST", str(request_path))
    monkeypatch.setattr(daemon, "SERVER_RESTART_ACK", str(ack_path))
    monkeypatch.setattr(daemon, "_wait_functional_server", lambda: True)
    original = daemon.fsutil.atomic_write

    def write_and_ack(path, data, **kwargs):
        original(path, data, **kwargs)
        request = json.loads(data)
        original(str(ack_path), json.dumps({"token": request["token"],
                                            "started": True}))

    monkeypatch.setattr(daemon.fsutil, "atomic_write", write_and_ack)
    assert daemon._request_mac_server_restart(timeout=0.1)


def test_source_start_supervisor_answers_restart_requests(monkeypatch):
    """python -m quassel.mac_app has no bundle entry; _supervise must answer too."""
    from quassel import mac_app
    seen = []
    monkeypatch.setattr(mac_app, "handle_server_restart", seen.append)
    monkeypatch.setattr(mac_app, "rotate_log", lambda _path: None)
    app = SimpleNamespace(enabled=True, daemon=SimpleNamespace(poll=lambda: None))
    mac_app.MacApp._supervise(app)
    assert seen == [app]


def _unit_exec(instance):
    """ExecStart of the profile template as systemd would hand it to /bin/sh."""
    import shlex
    from pathlib import Path
    unit = (Path(__file__).resolve().parents[1] / "systemd/quassel-server@.service").read_text()
    line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    line = line[len("ExecStart="):].replace("%i", instance).replace("%%", "%")
    return shlex.split(line)


@pytest.mark.skipif(os.name == "nt", reason="startet die systemd-ExecStart-Zeile über /bin/sh")
@pytest.mark.parametrize("name", ["next", "test-2", "a"])
def test_profile_unit_listens_on_the_profile_port(monkeypatch, name):
    import subprocess
    from quassel import profile
    argv = _unit_exec(name)
    env = {"PATH": os.environ["PATH"], "QUASSEL_PROFILE": name,
           "SERVER_BIN": "echo", "MODEL_PATH": "/m.bin"}
    out = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20).stdout
    monkeypatch.setenv("QUASSEL_PROFILE", name)
    monkeypatch.delenv("QUASSEL_PORT", raising=False)
    words = out.split()
    assert words[words.index("--port") + 1] == str(profile.port())
    assert words[words.index("--host") + 1] == "127.0.0.1"
    assert "-m" in words and "/m.bin" in words


@pytest.mark.skipif(os.name == "nt", reason='startet die systemd-ExecStart-Zeile über /bin/sh')
def test_profile_unit_honours_quassel_port():
    import subprocess
    env = {"PATH": os.environ["PATH"], "QUASSEL_PROFILE": "next", "QUASSEL_PORT": "9123",
           "SERVER_BIN": "echo", "MODEL_PATH": "/m.bin"}
    out = subprocess.run(_unit_exec("next"), env=env, capture_output=True,
                         text=True, timeout=20).stdout.split()
    assert out[out.index("--port") + 1] == "9123"


def test_profile_selects_its_own_server_unit(monkeypatch):
    from quassel import profile
    monkeypatch.delenv("QUASSEL_PROFILE", raising=False)
    assert profile.service() == "quassel-server.service"
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    assert profile.service() == "quassel-server@next.service"
