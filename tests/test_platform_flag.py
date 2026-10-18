"""Plattformzweige lesen das Modulkennzeichen IS_MAC statt sys.platform.

Tests wählen die Plattform, indem sie IS_MAC im geprüften Modul setzen. So
gelten sie unter Linux, Windows und macOS gleich.
"""
from types import SimpleNamespace

import pytest

from quassel import audio, coreaudio, daemon as daemon_mod, selfheal


def test_daemon_run_follows_is_mac(monkeypatch):
    calls = []
    monkeypatch.setattr(daemon_mod.Daemon, "_run_mac", lambda self: calls.append("mac"))
    monkeypatch.setattr(daemon_mod.Daemon, "_run_linux", lambda self: calls.append("linux"))
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    monkeypatch.setattr(daemon_mod, "IS_MAC", True)
    d.run()
    monkeypatch.setattr(daemon_mod, "IS_MAC", False)
    d.run()
    assert calls == ["mac", "linux"]


@pytest.mark.parametrize("is_mac, restart_name", [
    (True, "_request_mac_server_restart"),
    (False, "_restart_linux_server"),
])
def test_server_recovery_restart_follows_is_mac(monkeypatch, is_mac, restart_name):
    seen = []
    monkeypatch.setattr(selfheal, "recover", lambda restart=None: seen.append(restart))
    monkeypatch.setattr(daemon_mod, "IS_MAC", is_mac)
    daemon_mod._install_server_recovery()
    selfheal.recover()
    assert seen == [getattr(daemon_mod, restart_name)]


@pytest.mark.parametrize("is_mac, tool", [
    (True, "ffmpeg"),
    (False, "pw-record/parecord"),
])
def test_mic_error_names_recorder_tool_per_is_mac(monkeypatch, is_mac, tool):
    monkeypatch.setattr(daemon_mod, "IS_MAC", is_mac)
    monkeypatch.setattr(daemon_mod, "mac_backend", lambda: "ffmpeg")
    monkeypatch.setattr(daemon_mod, "record_command", lambda mic: None)
    d = SimpleNamespace(cfg=SimpleNamespace(mic="default"))
    assert daemon_mod.Daemon._mic_error_text(d) == daemon_mod.tr("err_recorder_missing", tool=tool)


@pytest.mark.parametrize("is_mac, cmd_tool", [
    (True, "ffmpeg"),
    (False, "pw-record"),
])
def test_record_command_follows_is_mac(monkeypatch, is_mac, cmd_tool):
    monkeypatch.setattr(audio, "IS_MAC", is_mac)
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/" + name)
    assert audio.record_command("default")[0] == cmd_tool


def test_list_mics_follows_is_mac(monkeypatch):
    pactl = SimpleNamespace(stdout="Name: pulse.in\nDescription: Pulse In\n")
    monkeypatch.setattr(audio, "_list_mics_mac", lambda: [("mac.in", "Mac In")])
    monkeypatch.setattr(audio.subprocess, "run", lambda *args, **kwargs: pactl)
    monkeypatch.setattr(audio, "IS_MAC", True)
    assert audio.list_mics() == [("mac.in", "Mac In")]
    monkeypatch.setattr(audio, "IS_MAC", False)
    assert audio.list_mics() == [("pulse.in", "Pulse In")]


@pytest.mark.parametrize("is_mac", [True, False])
def test_preroll_open_uses_sounddevice_only_on_mac(monkeypatch, is_mac):
    sd_calls, cmd_calls = [], []
    monkeypatch.setattr(audio, "IS_MAC", is_mac)
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    monkeypatch.setattr(audio, "_sd", lambda: sd_calls.append("sd"))
    monkeypatch.setattr(audio, "record_command", lambda mic: cmd_calls.append(mic))
    capture = audio._PrerollCapture("default", 300)
    assert capture.open() is False
    assert sd_calls == (["sd"] if is_mac else [])
    assert cmd_calls == ([] if is_mac else ["default"])


@pytest.mark.parametrize("is_mac, changed", [
    (True, True),
    (False, False),
])
def test_device_change_is_checked_only_on_mac(monkeypatch, is_mac, changed):
    monkeypatch.setattr(audio, "IS_MAC", is_mac)
    monkeypatch.setattr(coreaudio, "default_input_device", lambda: "new-device")
    capture = audio._PrerollCapture("default", 300)
    capture.device_id = "old-device"
    assert capture.device_changed() is changed


@pytest.mark.parametrize("is_mac", [True, False])
def test_start_settles_old_stream_only_on_mac(monkeypatch, tmp_path, is_mac):
    settled = []
    monkeypatch.setattr(audio, "IS_MAC", is_mac)
    monkeypatch.setattr(audio, "RUNDIR", str(tmp_path))
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    monkeypatch.setattr(audio, "_sd", lambda: None)
    monkeypatch.setattr(audio, "record_command", lambda mic: None)
    rec = audio.Recorder(raw_path=str(tmp_path / "rec.raw"), preroll_ms=None)
    monkeypatch.setattr(rec, "_settle_closing", lambda: settled.append("settle"))
    rec.start("default")
    assert settled == (["settle"] if is_mac else [])
