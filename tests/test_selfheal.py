"""Speech server recovery uses only a bounded, owned path."""
from quassel import selfheal, whisperclient


def test_probe_posts_silence_to_profile_path(monkeypatch):
    calls = []

    class Response:
        status = 200

        def read(self, _limit):
            return b""

    class Connection:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def request(self, method, path, body, headers):
            calls.append((method, path, body, headers))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(whisperclient, "server_up", lambda timeout: True)
    monkeypatch.setattr(whisperclient, "server_url", lambda: "http://127.0.0.1:8765/q/secret")
    monkeypatch.setattr(selfheal.profile, "port", lambda: 8765)
    monkeypatch.setattr(selfheal.http.client, "HTTPConnection", Connection)
    assert selfheal.functional_probe()
    method, path, body, _ = calls[1]
    assert (method, path) == ("POST", "/q/secret/inference")
    assert b"RIFF" in body and b"\0" * 100 in body


def test_recovery_never_restarts_healthy_server():
    calls = []
    assert not selfheal.recover(lambda: True, lambda: calls.append("restart"))
    assert calls == []


def test_recovery_restarts_once_and_probes_again():
    probes = iter((False, True))
    calls = []
    assert selfheal.recover(lambda: next(probes), lambda: calls.append(1) or True)
    assert calls == [1]


def test_daemon_crash_is_visible_and_persisted(monkeypatch):
    from types import SimpleNamespace
    from quassel import state
    calls = []
    monkeypatch.setattr(state, "state_set", lambda *args: calls.append(("state", args)))
    tray = SimpleNamespace(set_mode=lambda *args: calls.append(("tray", args)),
                           showMessage=lambda *args: calls.append(("notice", args)))
    pill = SimpleNamespace(on=False,
                           set_mode=lambda *args: calls.append(("pill", args)))
    selfheal.report_daemon_crash(SimpleNamespace(tray=tray, pill=pill), 1)
    assert pill.on
    assert [name for name, _ in calls] == ["state", "tray", "notice", "pill"]
    assert calls[0][1][0] == "error"
