"""A slow GPU load must not permanently replace the selected engine."""
from unittest.mock import MagicMock

from quassel.win import server


def test_slow_gpu_keeps_running_and_does_not_activate_cpu(monkeypatch):
    proc = MagicMock()
    proc.poll.return_value = None
    monkeypatch.setattr(server, "_proc", proc)
    monkeypatch.setattr(server, "start", lambda: None)
    monkeypatch.setattr(server, "server_up", lambda: False)
    monkeypatch.setattr(server.time, "sleep", lambda _: None)
    events = []
    monkeypatch.setattr(server, "stop", lambda: events.append("stop"))
    monkeypatch.setattr(server, "_activate_engine", lambda kind, progress: events.append(kind))
    monkeypatch.setattr(server, "_provide_engine_zip", lambda *a: events.append("download"))
    assert server.ensure_working(lambda frac, what: events.append(what)) is False
    assert "stop" not in events and "cpu" not in events and "download" not in events
    assert any("loading" in event.lower() for event in events)


def test_cpu_emergency_engine_retries_preferred_engine_next_time(monkeypatch, tmp_path):
    kind = server.preferred_engine_kind()
    archive = tmp_path / kind / server.ENGINE_ZIPS[kind]
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"fixture")
    monkeypatch.setattr(server, "ENGINES_DIR", str(tmp_path))
    env = {"ENGINE_RETRY": "gpu", "SERVER_BIN": "cpu.exe"}
    monkeypatch.setattr(server.config, "read_serverenv", lambda: env.copy())
    def save(value):
        env.clear()
        env.update(value)
    monkeypatch.setattr(server.config, "write_serverenv", save)
    monkeypatch.setattr(server, "server_exe", lambda: "gpu.exe")
    monkeypatch.setattr(server, "server_up", lambda: True)
    events = []
    monkeypatch.setattr(server, "stop", lambda: events.append("stop"))
    monkeypatch.setattr(server, "_activate_engine", lambda name, progress: events.append(name) or True)
    monkeypatch.setattr(server, "start", lambda: events.append("start"))
    assert server.ensure_working() is True
    assert events == ["stop", kind, "start"]
    assert env["SERVER_BIN"] == "gpu.exe"
    assert "ENGINE_RETRY" not in env
