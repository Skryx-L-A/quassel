"""Lebensnachweis des Diktatdienstes (state.DAEMON_PID) für CLI und MCP auf
macOS und Windows, wo es kein systemctl gibt."""
import os
import stat

import pytest

from quassel import ask, cli, state


@pytest.fixture
def pidfile(tmp_path, monkeypatch):
    path = tmp_path / "daemon.pid"
    monkeypatch.setattr(state, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(state, "DAEMON_PID", str(path))
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    return path


def test_service_writes_its_pid_privately(pidfile, monkeypatch):
    registered = []
    monkeypatch.setattr("atexit.register", registered.append)
    state.mark_daemon_running()
    assert pidfile.read_text().strip() == str(os.getpid())
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(pidfile).st_mode) == 0o600
    registered[0]()                       # Ende: eigene PID wird entfernt
    assert not pidfile.exists()


def test_cleanup_removes_the_pid_file_after_closing_it(pidfile, monkeypatch):
    """Windows löscht keine offene Datei: das Entfernen darf nicht innerhalb des
    open() stehen, sonst bleibt die PID stehen und CLI/MCP halten den Dienst für
    lebendig."""
    registered = []
    monkeypatch.setattr("atexit.register", registered.append)
    state.mark_daemon_running()
    real_remove = os.remove
    open_files = []
    real_open = open

    def tracking_open(file, *a, **kw):
        f = real_open(file, *a, **kw)
        if str(file) == str(pidfile):
            open_files.append(f)
        return f

    def strict_remove(path):
        assert all(f.closed for f in open_files), "Datei noch offen beim Entfernen"
        real_remove(path)
    monkeypatch.setattr("builtins.open", tracking_open)
    monkeypatch.setattr(state.os, "remove", strict_remove)
    registered[0]()
    assert not pidfile.exists()


def test_cleanup_leaves_a_newer_service_alone(pidfile, monkeypatch):
    registered = []
    monkeypatch.setattr("atexit.register", registered.append)
    state.mark_daemon_running()
    pidfile.write_text("999999\n")        # ein neuerer Dienst hat übernommen
    registered[0]()
    assert pidfile.read_text().strip() == "999999"


def test_ask_needs_a_live_service_on_macos_and_windows(pidfile, monkeypatch):
    assert cli._daemon_status() == "unknown" and cli._daemon_answers() is False
    pidfile.write_text("%d\n" % os.getpid())
    assert cli._daemon_status() == "running" and cli._daemon_answers() is True
    monkeypatch.setattr(ask, "pid_alive", lambda pid: False)
    assert cli._daemon_status() == "stopped" and cli._daemon_answers() is False
