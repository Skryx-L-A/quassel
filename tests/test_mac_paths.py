"""Tests der darwin-Zweige in config/state/audio/beep.

Pfad-Konstanten (config/state) entstehen beim Modul-Import aus sys.platform —
statt riskanter importlib.reload-Spielereien (hinterlassen anderen Modulen
veraltete Objekt-Referenzen) prüft ein SUBPROZESS mit vorab gesetztem
sys.platform den jeweiligen Zweig. Kein globaler Interpreter-Zustand wird
angefasst; audio/beep nutzen pytest-monkeypatch (auto-restauriert).
"""
import json
import os
import subprocess
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import audio, beep

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _module_attrs(platform_name, module, attrs):
    """quassel.<module> in einem frischen Interpreter mit gefälschtem
    sys.platform importieren und Attribute als JSON zurückholen."""
    code = (
        "import sys, json\n"
        f"sys.platform = {platform_name!r}\n"
        f"import quassel.{module} as m\n"
        f"print(json.dumps({{a: getattr(m, a) for a in {attrs!r}}}))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=30, check=False, cwd=_REPO)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ---------------------------------------------------------------- config/state
def test_config_paths_darwin():
    got = _module_attrs("darwin", "config",
                        ["CONFDIR", "DATADIR", "CONFIG", "SERVERENV"])
    expect = os.path.expanduser("~/Library/Application Support/Quassel")
    assert got["CONFDIR"] == expect
    assert got["DATADIR"] == expect
    assert got["CONFIG"] == os.path.join(expect, "config.ini")
    assert got["SERVERENV"] == os.path.join(expect, "server.env")


def test_config_paths_linux_unchanged():
    got = _module_attrs("linux", "config", ["CONFDIR", "DATADIR"])
    assert "Application Support" not in got["CONFDIR"]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    assert got["CONFDIR"].endswith(os.path.join(".config", "quassel")) \
        or (xdg and got["CONFDIR"] == os.path.join(xdg, "quassel"))


def test_state_rundir_darwin():
    got = _module_attrs("darwin", "state", ["RUNDIR", "STATE"])
    # Application Support, NICHT Caches: Caches darf das System bei
    # Plattenplatzdruck leeren — auch mitten in einer Aufnahme.
    expect = os.path.expanduser("~/Library/Application Support/Quassel/run")
    assert got["RUNDIR"] == expect
    assert got["STATE"] == os.path.join(expect, "state.json")


def test_state_rundir_linux_unchanged():
    got = _module_attrs("linux", "state", ["RUNDIR"])
    assert "Library" not in got["RUNDIR"]


# --------------------------------------------------------------------- audio
def test_record_command_darwin_default(monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.setattr(audio.shutil, "which",
                        lambda name: "/opt/homebrew/bin/ffmpeg")
    cmd = audio.record_command()
    assert cmd[0] == "ffmpeg"
    assert ":default" in cmd
    assert cmd[-1] == "-"
    i = cmd.index("-ar")
    assert cmd[i + 1] == str(audio.RATE)


def test_record_command_darwin_named_mic(monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    cmd = audio.record_command("MacBook Pro Microphone")
    assert ":MacBook Pro Microphone" in cmd


def test_record_command_darwin_no_ffmpeg(monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    assert audio.record_command() is None


def test_list_mics_mac_parses_ffmpeg_output(monkeypatch):
    stderr = (
        "[AVFoundation indev @ 0x1] AVFoundation video devices:\n"
        "[AVFoundation indev @ 0x1] [0] FaceTime Camera\n"
        "[AVFoundation indev @ 0x1] AVFoundation audio devices:\n"
        "[AVFoundation indev @ 0x1] [0] APLTF\n"
        "[AVFoundation indev @ 0x1] [1] MacBook Pro Microphone\n")
    monkeypatch.setattr(audio.subprocess, "run",
                        lambda *a, **kw: MagicMock(stderr=stderr))
    mics = audio._list_mics_mac()
    assert mics == [("APLTF", "APLTF"),
                    ("MacBook Pro Microphone", "MacBook Pro Microphone")]


def test_list_mics_mac_survives_missing_ffmpeg(monkeypatch):
    def boom(*a, **kw):
        raise OSError("ffmpeg fehlt")
    monkeypatch.setattr(audio.subprocess, "run", boom)
    assert audio._list_mics_mac() == []


def test_list_mics_dispatches_to_mac(monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.setattr(audio, "_list_mics_mac", lambda: [("a", "a")])
    assert audio.list_mics() == [("a", "a")]


# ---------------------------------------------------------------------- beep
def test_beep_uses_afplay_on_darwin(tmp_path, monkeypatch):
    wav = tmp_path / "start.wav"
    wav.write_bytes(b"RIFF")
    calls = []
    monkeypatch.setattr(beep.sys, "platform", "darwin")
    monkeypatch.setattr(beep.subprocess, "Popen",
                        lambda args, **kw: calls.append(args))
    beep._play(str(wav))
    assert calls and calls[0][:2] == ["afplay", str(wav)]
