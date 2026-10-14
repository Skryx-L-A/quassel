"""Einzelinstanz-Kanal der Windows-App je Benutzer und Sitzung (Audit A11).

QLocalServer nutzt unter Windows eine Named Pipe im globalen Namensraum.
Ein fester Name ("quassel-app") galt für alle Benutzer: beim schnellen
Benutzerwechsel beendete sich die Instanz des zweiten Nutzers sofort."""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import test_win_stubs as win_stubs

WIN_APP = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "quassel", "win", "app.py")


def test_two_users_get_two_channels(monkeypatch):
    app = win_stubs.load_win_app(monkeypatch)
    monkeypatch.delenv("QUASSEL_PROFILE", raising=False)
    assert app.ipc_name("alice", 1) != app.ipc_name("bob", 2)
    assert app.ipc_name("alice", 1) != app.ipc_name("alice", 2)


def test_the_name_is_safe_for_a_pipe(monkeypatch):
    app = win_stubs.load_win_app(monkeypatch)
    monkeypatch.delenv("QUASSEL_PROFILE", raising=False)
    name = app.ipc_name("Max Müller\\x", 3)
    assert name == "quassel-app-Max_M_ller_x-s3"


def test_profile_and_missing_session(monkeypatch):
    app = win_stubs.load_win_app(monkeypatch)
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    # ohne angegebene Sitzung fragt ipc_name Windows; "fehlt" heißt, sie ist
    # nicht zu ermitteln (auf einem echten Windows ist sie es)
    monkeypatch.setattr(app, "_session_id", lambda: None)
    assert app.ipc_name("alice", None) == "quassel-app-next-alice"


def test_server_and_probe_use_the_same_per_user_name():
    """Kein fester Kanalname mehr im Code: Erstinstanz und Prüfung eines
    zweiten Starts nutzen beide ipc_name()."""
    with open(WIN_APP, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    literals = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and node.value == "quassel-app"]
    assert literals == []
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in ("listen", "connectToServer", "removeServer")]
    assert len(calls) == 3
    for call in calls:
        arg = call.args[0]
        assert isinstance(arg, (ast.Name, ast.Call)), ast.dump(arg)


def test_access_is_limited_to_the_user():
    with open(WIN_APP, encoding="utf-8") as f:
        src = f.read()
    assert "UserAccessOption" in src
    from PySide6.QtNetwork import QLocalServer
    assert hasattr(QLocalServer.SocketOption, "UserAccessOption")
