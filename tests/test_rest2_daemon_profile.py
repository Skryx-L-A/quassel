"""Linux-Profil für den Diktat-Dienst: die Vorlage systemd/quasseld@.service
und dass Rohtext-Anfrage, Status und An/Aus nur die Units des eigenen Profils
ansprechen (QUASSEL_PROFILE). Kein systemctl, kein echter Dienst."""
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import cli, profile                         # noqa: E402

UNIT = Path(__file__).resolve().parents[1] / "systemd" / "quasseld@.service"


def _lines(instance, home="/home/u"):
    text = UNIT.read_text().replace("%i", instance).replace("%h", home)
    return [l for l in text.splitlines() if l and not l.startswith("#")]


def _value(lines, key):
    return [l[len(key) + 1:] for l in lines if l.startswith(key + "=")]


# ----------------------------------------------------------------- Vorlage
@pytest.mark.parametrize("name", ["next", "test-2"])
def test_template_sets_the_profile_and_its_own_paths(name):
    lines = _lines(name)
    assert _value(lines, "Environment") == [f"QUASSEL_PROFILE={name}"]
    assert _value(lines, "EnvironmentFile") == [f"-/home/u/.config/quassel-{name}/daemon.env"]
    wants = _value(lines, "Wants")[0].split()
    assert f"quassel-server@{name}.service" in wants
    # Die Pille der installierten App gehört nicht zu diesem Profil.
    assert "quassel-pill.service" not in wants


def _exec_argv(name, home):
    line = _value(_lines(name, home), "ExecStart")[0].replace("%%", "%")
    return shlex.split(line)


def test_template_runs_the_installed_launcher_with_the_profile(tmp_path):
    launcher = tmp_path / ".local" / "bin" / "quasseld"
    launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/bin/sh\necho "profile=$QUASSEL_PROFILE"\n')
    launcher.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "QUASSEL_PROFILE": "next"}
    out = subprocess.run(_exec_argv("next", str(tmp_path)), env=env,
                         capture_output=True, text=True, timeout=20).stdout
    assert out.strip() == "profile=next"


def test_template_honours_quasseld_bin(tmp_path):
    other = tmp_path / "checkout-quasseld"
    other.write_text('#!/bin/sh\necho "other $QUASSEL_PROFILE"\n')
    other.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "QUASSEL_PROFILE": "next",
           "QUASSELD_BIN": str(other)}
    out = subprocess.run(_exec_argv("next", str(tmp_path)), env=env,
                         capture_output=True, text=True, timeout=20).stdout
    assert out.strip() == "other next"


def test_template_matches_the_base_unit_otherwise():
    base = (UNIT.parent / "quasseld.service").read_text()
    for key in ("Type=simple", "Restart=on-failure", "WantedBy=default.target"):
        assert key in base and key in UNIT.read_text()


# -------------------------------------------------------- CLI raw-last
class Run:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **_kw):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=0)


@pytest.mark.parametrize("name,unit", [("", "quasseld.service"),
                                       ("next", "quasseld@next.service")])
def test_raw_last_signals_the_daemon_of_its_own_profile(monkeypatch, capsys, name, unit):
    if name:
        monkeypatch.setenv("QUASSEL_PROFILE", name)
    else:
        monkeypatch.delenv("QUASSEL_PROFILE", raising=False)
    run = Run()
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli.main(["raw-last"]) == 0
    assert run.calls[0] == ["systemctl", "--user", "is-active", "--quiet", unit]
    assert run.calls[-1] == ["systemctl", "--user", "kill", "-s", "HUP", unit]


# ------------------------------------------- Kontrollzentrum und Pille
qt = pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from quassel import center, pill_qt                     # noqa: E402


def test_center_units_for_the_installed_app(monkeypatch):
    monkeypatch.delenv("QUASSEL_PROFILE", raising=False)
    assert center.daemon_unit() == "quasseld.service"
    assert center.service_units(start=True) == [
        "quasseld.service", "quassel-server.service", "quassel-pill"]
    assert center.service_units(start=False) == [
        "quasseld.service", "quassel-server.service", "quassel-ydotoold"]


def test_center_units_of_a_profile_leave_shared_services_alone(monkeypatch):
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    assert center.daemon_unit() == "quasseld@next.service"
    for start in (True, False):
        assert center.service_units(start=start) == [
            "quasseld@next.service", "quassel-server@next.service"]


def test_center_raw_back_signals_its_own_profile(monkeypatch):
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    monkeypatch.setattr(center, "IS_WINDOWS", False)
    monkeypatch.setattr(center, "IS_MAC", False)
    sent = []
    monkeypatch.setattr(center, "sysctl",
                        lambda *a: sent.append(a) or SimpleNamespace(returncode=0))
    fake = SimpleNamespace(controller=None,
                           raw_back_status=SimpleNamespace(setText=lambda t: None))
    center.Center.on_raw_back(fake)
    assert sent == [("kill", "-s", "HUP", "quasseld@next.service")]


def test_pill_toggle_of_a_profile_never_stops_the_shared_ydotoold(monkeypatch):
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    monkeypatch.setattr(pill_qt.sys, "platform", "linux")
    monkeypatch.setattr(pill_qt, "TOGGLE", None)
    monkeypatch.setattr(pill_qt, "daemon_active", lambda: True)
    monkeypatch.setattr(pill_qt, "daemon_active_invalidate", lambda: None)
    calls = []
    monkeypatch.setattr(pill_qt.subprocess, "run",
                        lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0))
    fake = SimpleNamespace(set_mode=lambda m: None, on=True)
    pill_qt.Pill._toggle(fake)
    assert calls == [["systemctl", "--user", "stop",
                      "quasseld@next.service", "quassel-server@next.service"]]
