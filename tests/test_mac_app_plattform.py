"""Controller toggle tests without starting the app or whisper-server."""
from quassel import mac_app


def test_toggle_off_unloads_owned_server(monkeypatch):
    app = mac_app.MacApp(None)
    app.enabled = True
    calls = []

    def stop_daemon():
        calls.append("daemon.stop")
        app.enabled = False

    monkeypatch.setattr(app, "_stop_daemon", stop_daemon)
    monkeypatch.setattr(mac_app.server_mac, "stop", lambda: calls.append("server.stop"))
    monkeypatch.setattr(app, "_sync_ui", lambda: calls.append("ui"))
    app.toggle()
    assert calls == ["daemon.stop", "server.stop", "ui"]


def test_toggle_on_loads_server_before_daemon(monkeypatch):
    app = mac_app.MacApp(None)
    calls = []
    monkeypatch.setattr(mac_app.server_mac, "start", lambda: calls.append("server.start"))
    monkeypatch.setattr(app, "_start_daemon", lambda: calls.append("daemon.start"))
    monkeypatch.setattr(app, "_sync_ui", lambda: calls.append("ui"))
    app.toggle()
    assert calls == ["server.start", "daemon.start", "ui"]
