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
import time
from unittest.mock import MagicMock

import pytest

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
        "[AVFoundation indev @ 0x1] [0] Bluetooth-Kopfhörer\n"
        "[AVFoundation indev @ 0x1] [1] MacBook Pro Microphone\n")
    monkeypatch.setattr(audio.subprocess, "run",
                        lambda *a, **kw: MagicMock(stderr=stderr))
    mics = audio._list_mics_mac_ffmpeg()
    assert mics == [("Bluetooth-Kopfhörer", "Bluetooth-Kopfhörer"),
                    ("MacBook Pro Microphone", "MacBook Pro Microphone")]


def test_list_mics_mac_survives_missing_ffmpeg(monkeypatch):
    def boom(*a, **kw):
        raise OSError("ffmpeg fehlt")
    monkeypatch.setattr(audio.subprocess, "run", boom)
    assert audio._list_mics_mac_ffmpeg() == []


def test_list_mics_dispatches_to_mac(monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.setattr(audio, "_list_mics_mac", lambda: [("a", "a")])
    assert audio.list_mics() == [("a", "a")]


# ------------------------------------------------- macOS-Aufnahmebackend
def _fake_sd(devices):
    sd = MagicMock()
    sd.query_devices.side_effect = \
        lambda i=None: devices if i is None else devices[i]
    sd.default.device = [0, 1]
    return sd


DEVICES = [
    {"name": "Bluetooth-Kopfhörer", "max_input_channels": 1, "default_samplerate": 24000.0},
    {"name": "Lautsprecher", "max_input_channels": 0,
     "default_samplerate": 48000.0},
    {"name": "MacBook Pro-Mikrofon", "max_input_channels": 1,
     "default_samplerate": 48000.0},
]


def test_mac_backend_default_is_sounddevice(monkeypatch):
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    assert audio.mac_backend() == "sd16"


def test_mac_backend_env_switches_and_rejects_unknown(monkeypatch):
    monkeypatch.setenv("QUASSEL_MAC_AUDIO", "ffmpeg")
    assert audio.mac_backend() == "ffmpeg"
    monkeypatch.setenv("QUASSEL_MAC_AUDIO", "sdnative")
    assert audio.mac_backend() == "sdnative"
    monkeypatch.setenv("QUASSEL_MAC_AUDIO", "quatsch")
    assert audio.mac_backend() == audio.MAC_BACKEND_DEFAULT


def test_list_mics_mac_uses_sounddevice(monkeypatch):
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    monkeypatch.setattr(audio, "_sd", lambda: _fake_sd(DEVICES))
    # nur Eingänge, Ausgabegeräte fallen raus
    assert audio._list_mics_mac() == [
        ("Bluetooth-Kopfhörer", "Bluetooth-Kopfhörer"),
        ("MacBook Pro-Mikrofon", "MacBook Pro-Mikrofon")]


def test_list_mics_mac_falls_back_to_ffmpeg_for_ffmpeg_backend(monkeypatch):
    monkeypatch.setenv("QUASSEL_MAC_AUDIO", "ffmpeg")
    monkeypatch.setattr(audio, "_list_mics_mac_ffmpeg", lambda: [("x", "x")])
    assert audio._list_mics_mac() == [("x", "x")]


def test_list_mics_mac_falls_back_without_sounddevice(monkeypatch):
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    monkeypatch.setattr(audio, "_sd", lambda: None)
    monkeypatch.setattr(audio, "_list_mics_mac_ffmpeg", lambda: [("y", "y")])
    assert audio._list_mics_mac() == [("y", "y")]


def test_mac_device_resolves_name_index_and_default(monkeypatch):
    monkeypatch.setattr(audio, "_sd", lambda: _fake_sd(DEVICES))
    assert audio._mac_device("MacBook Pro-Mikrofon") == 2
    assert audio._mac_device("1") == 1          # Index-String bleibt Index
    assert audio._mac_device("default") is None
    assert audio._mac_device("") is None
    assert audio._mac_device("gibt es nicht") is None


def test_mac_device_rate(monkeypatch):
    monkeypatch.setattr(audio, "_sd", lambda: _fake_sd(DEVICES))
    assert audio._mac_device_rate(2) == 48000
    assert audio._mac_device_rate(0) == 24000   # Bluetooth-Headset
    assert audio._mac_device_rate(None) == 24000  # Standardgerät (Index 0)


def test_recorder_uses_mac_stream_on_darwin(monkeypatch, tmp_path):
    """macOS ohne ffmpeg-Backend: kein Subprozess, sondern der Stream."""
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    monkeypatch.setattr(audio.os, "makedirs", lambda *a, **kw: None)

    def boom(*a, **kw):
        raise AssertionError("record_command darf hier nicht benutzt werden")
    monkeypatch.setattr(audio, "record_command", boom)

    stream = MagicMock(active=True, started=1.0)
    stream.start.return_value = True
    monkeypatch.setattr(audio, "_MacStream", lambda *a, **kw: stream)

    rec = audio.Recorder(raw_path=str(tmp_path / "rec.raw"))
    assert rec.start("default") is True
    assert rec.proc is None and rec.mac is stream
    assert rec.active is True
    rec.stop()
    stream.stop.assert_called_once()
    assert rec.mac is None


def test_recorder_start_fails_when_stream_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    monkeypatch.setattr(audio.os, "makedirs", lambda *a, **kw: None)
    stream = MagicMock()
    stream.start.return_value = False
    monkeypatch.setattr(audio, "_MacStream", lambda *a, **kw: stream)
    rec = audio.Recorder(raw_path=str(tmp_path / "rec.raw"))
    assert rec.start("default") is False
    assert rec.mac is None


def test_recorder_uses_subprocess_for_ffmpeg_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.setenv("QUASSEL_MAC_AUDIO", "ffmpeg")
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio, "_bluez_profiles", lambda: {})
    popen = MagicMock(return_value=MagicMock(poll=lambda: None))
    monkeypatch.setattr(audio.subprocess, "Popen", popen)
    rec = audio.Recorder(raw_path=str(tmp_path / "rec.raw"))
    assert rec.start("default") is True
    assert rec.mac is None
    assert popen.call_args[0][0][0] == "ffmpeg"
    rec.outfile.close()


def test_recorder_unchanged_on_linux(monkeypatch, tmp_path):
    """Linux nimmt weiterhin über den Subprozess auf — kein Stream."""
    monkeypatch.setattr(audio.sys, "platform", "linux")
    monkeypatch.setattr(audio, "record_command", lambda mic: ["pw-record", "-"])
    monkeypatch.setattr(audio, "_bluez_profiles", lambda: {})
    popen = MagicMock(return_value=MagicMock(poll=lambda: None))
    monkeypatch.setattr(audio.subprocess, "Popen", popen)
    rec = audio.Recorder(raw_path=str(tmp_path / "rec.raw"))
    assert rec.start("default") is True
    assert rec.mac is None and popen.called
    rec.outfile.close()


# ------------------------------------------------------------ Resampling
def test_polyphase_length_gain_and_streaming():
    """Der eigene Resampler (Backend "sdnative") muss Länge und Pegel treffen
    und blockweise dasselbe liefern wie am Stück — sonst knackst es an den
    Blockgrenzen."""
    np = pytest.importorskip("numpy")

    def tone(freq, seconds, rate, amp=10000):
        t = np.arange(int(seconds * rate)) / rate
        return (amp * np.sin(2 * np.pi * freq * t)).astype("<i2").tobytes()

    for src in (24000, 44100, 48000):
        out = audio._Polyphase(src, audio.RATE).feed(tone(1000, 1.0, src))
        y = np.frombuffer(out, "<i2").astype(float)
        assert y.size == audio.RATE                       # exakt 1 s
        assert abs(np.sqrt((y ** 2).mean()) - 7071) < 60  # Pegel erhalten

    data = tone(800, 0.4, 48000)
    whole = audio._Polyphase(48000, audio.RATE).feed(data)
    chunked = audio._Polyphase(48000, audio.RATE)
    pieces = b"".join(chunked.feed(data[i:i + 2048])
                      for i in range(0, len(data), 2048))
    assert whole == pieces


def test_polyphase_filters_above_nyquist():
    """Über 8 kHz muss weggefiltert werden, sonst spiegelt es sich ins Band."""
    np = pytest.importorskip("numpy")

    def rms_at(freq):
        t = np.arange(int(0.8 * 48000)) / 48000
        data = (10000 * np.sin(2 * np.pi * freq * t)).astype("<i2").tobytes()
        y = np.frombuffer(audio._Polyphase(48000, audio.RATE).feed(data),
                          "<i2").astype(float)[6000:]
        return float(np.sqrt((y ** 2).mean()))

    assert rms_at(1000) > 7000        # Durchlassband unangetastet
    assert rms_at(5000) > 7000
    assert rms_at(9000) < 70          # Sperrband: < -40 dB
    assert rms_at(12000) < 70


# ---------------------------------------------------------------------- beep
def test_beep_uses_afplay_on_darwin(tmp_path, monkeypatch):
    """Lässt sich keine Ausgabe-Einheit öffnen, bleibt afplay der Weg auf
    darwin. Gespielt wird im Abspiel-Thread, deshalb wird auf den Aufruf
    gewartet statt sofort geprüft. Die eigene Ausgabe-Einheit hat eine eigene
    Testdatei (test_beep_mac.py)."""
    wav = tmp_path / "start.wav"
    wav.write_bytes(b"RIFF")
    calls = []

    def kein_geraet(*_args, **_kw):
        raise OSError("kein Ausgabegerät")

    monkeypatch.setattr(beep.sys, "platform", "darwin")
    monkeypatch.setattr(beep, "_open_unit", kein_geraet)   # nie ein echtes Gerät
    monkeypatch.setattr(beep.subprocess, "Popen",
                        lambda args, **kw: calls.append(args))
    player = beep._MacPlayer()
    monkeypatch.setattr(beep, "_PLAYER", player)
    try:
        beep._play(str(wav))
        ende = time.monotonic() + 3.0
        while not calls and time.monotonic() < ende:
            time.sleep(0.005)
    finally:
        player.close()
    assert calls and calls[0][:2] == ["afplay", str(wav)]
