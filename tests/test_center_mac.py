"""macOS-Zweige des Kontrollzentrums: kein systemctl, Autostart via
LaunchAgent (Plist + launchctl), Server-Neustart über server_mac."""
import os
import plistlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from quassel import center  # noqa: E402


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(center, "IS_WINDOWS", False)
    monkeypatch.setattr(center, "IS_MAC", True)


@pytest.fixture
def agent_plist(tmp_path, monkeypatch):
    p = tmp_path / "de.skryx.quassel.plist"
    monkeypatch.setattr(center, "LAUNCH_AGENT_PLIST", str(p))
    return p


@pytest.fixture
def runs(monkeypatch):
    calls = []
    monkeypatch.setattr(center.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd))
    return calls


def test_sysctl_is_noop_on_mac(mac, runs):
    r = center.sysctl("is-enabled", "--quiet", "quasseld")
    assert r.returncode == 1
    assert runs == []           # nie systemctl aufrufen


def test_daemon_active_true_on_mac(mac, runs):
    assert center.daemon_active() is True
    assert runs == []


def test_autostart_enabled_reflects_plist(mac, agent_plist):
    assert center.autostart_enabled() is False
    agent_plist.write_bytes(b"x")
    assert center.autostart_enabled() is True


def test_autostart_on_writes_plist_and_bootstraps(mac, agent_plist, runs,
                                                  monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    center.mac_autostart_set(True)
    with open(agent_plist, "rb") as f:
        plist = plistlib.load(f)
    assert plist["Label"] == "de.skryx.quassel"
    assert plist["ProgramArguments"] == ["/usr/bin/open", "-a", "Quassel"]
    assert plist["RunAtLoad"] is True
    # bootout vor bootstrap: macht doppeltes Aktivieren idempotent
    assert runs == [["launchctl", "bootout",
                     f"gui/{os.getuid()}/de.skryx.quassel"],
                    ["launchctl", "bootstrap", f"gui/{os.getuid()}",
                     str(agent_plist)]]


def test_autostart_on_frozen_uses_bundle_path(mac, agent_plist, runs,
                                              monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable",
                        "/Applications/Quassel.app/Contents/MacOS/Quassel")
    center.mac_autostart_set(True)
    with open(agent_plist, "rb") as f:
        plist = plistlib.load(f)
    assert plist["ProgramArguments"] == ["/usr/bin/open",
                                         "/Applications/Quassel.app"]


def test_autostart_off_boots_out_and_removes_plist(mac, agent_plist, runs):
    agent_plist.write_bytes(b"x")
    center.mac_autostart_set(False)
    assert not agent_plist.exists()
    assert runs == [["launchctl", "bootout",
                     f"gui/{os.getuid()}/de.skryx.quassel"]]


def test_autostart_off_without_plist_is_quiet(mac, agent_plist, runs):
    center.mac_autostart_set(False)     # kein Plist vorhanden: kein Fehler
    assert not agent_plist.exists()


def test_mac_app_path_none_when_not_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert center.mac_app_path() is None


def test_restart_server_uses_server_mac(mac, monkeypatch):
    from quassel import server_mac
    calls = []
    monkeypatch.setattr(server_mac, "kill_orphans",
                        lambda: calls.append("kill"))
    monkeypatch.setattr(server_mac, "port_in_use", lambda **kw: False)
    monkeypatch.setattr(server_mac, "start", lambda: calls.append("start"))
    center.restart_server()
    assert calls == ["kill", "start"]
