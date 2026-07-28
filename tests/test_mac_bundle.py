"""Tests der Frozen-Pfad-Auflösung fürs macOS-.app-Bundle (PyInstaller).

Gefroren ist sys.executable .../Quassel.app/Contents/MacOS/Quassel und die
Kindprozesse laufen als Subkommando derselben exe statt "python -m ...".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import mac_app, server_mac


def _fake_bundle(tmp_path, with_server=True):
    """Minimale .app-Struktur: MacOS/Quassel + Resources/whisper/whisper-server."""
    contents = tmp_path / "Quassel.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    exe = contents / "MacOS" / "Quassel"
    exe.write_text("")
    if with_server:
        wdir = contents / "Resources" / "whisper"
        wdir.mkdir(parents=True)
        server = wdir / "whisper-server"
        server.write_text("")
        server.chmod(0o755)
    return exe


def test_bundled_server_bin_unfrozen():
    assert server_mac.bundled_server_bin() is None


def test_bundled_server_bin_frozen(tmp_path, monkeypatch):
    exe = _fake_bundle(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    expect = str(exe.parent.parent / "Resources" / "whisper" / "whisper-server")
    assert server_mac.bundled_server_bin() == expect


def test_bundled_server_bin_frozen_missing(tmp_path, monkeypatch):
    exe = _fake_bundle(tmp_path, with_server=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert server_mac.bundled_server_bin() is None


def test_server_bin_prefers_env_then_bundle(tmp_path, monkeypatch):
    exe = _fake_bundle(tmp_path)
    bundled = str(exe.parent.parent / "Resources" / "whisper" / "whisper-server")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    # server.env leer -> Bundle gewinnt
    monkeypatch.setattr(server_mac.config, "read_serverenv", lambda: {})
    assert server_mac.server_bin() == bundled
    # gültiger server.env-Eintrag gewinnt vor dem Bundle
    other = tmp_path / "own-server"
    other.write_text("")
    other.chmod(0o755)
    monkeypatch.setattr(server_mac.config, "read_serverenv",
                        lambda: {"SERVER_BIN": str(other)})
    assert server_mac.server_bin() == str(other)


def test_child_commands_unfrozen():
    assert mac_app.daemon_command() == [sys.executable, "-m", "quassel.daemon"]
    assert mac_app.center_command() == [sys.executable, "-m", "quassel.center"]


def test_child_commands_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert mac_app.daemon_command() == [sys.executable, "daemon"]
    assert mac_app.center_command() == [sys.executable, "center"]


def test_augment_path_idempotent(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    mac_app.augment_path()
    first = os.environ["PATH"]
    for p in mac_app.BREW_PATHS:
        if os.path.isdir(p):
            assert p in first.split(os.pathsep)
    mac_app.augment_path()
    assert os.environ["PATH"] == first


def test_check_ffmpeg_notifies_when_missing(monkeypatch):
    monkeypatch.setenv("QUASSEL_MAC_AUDIO", "ffmpeg")
    monkeypatch.setattr(mac_app.shutil, "which", lambda _: None)
    calls = []

    class Tray:
        def showMessage(self, title, text):
            calls.append((title, text))

    assert mac_app.check_ffmpeg(Tray()) is False
    assert calls and "brew install ffmpeg" in calls[0][1]
    monkeypatch.setattr(mac_app.shutil, "which", lambda _: "/opt/homebrew/bin/ffmpeg")
    assert mac_app.check_ffmpeg(None) is True


def test_check_ffmpeg_silent_on_sounddevice_backend(monkeypatch):
    """Der Auslieferungs-Default nimmt ohne ffmpeg auf — dann darf beim Start
    keine Notification behaupten, ohne ffmpeg gehe die Aufnahme nicht."""
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    monkeypatch.setattr(mac_app.shutil, "which", lambda _: None)
    calls = []

    class Tray:
        def showMessage(self, title, text):
            calls.append((title, text))

    assert mac_app.check_ffmpeg(Tray()) is True
    assert calls == []
