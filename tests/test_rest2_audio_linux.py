"""Linux-Aufnahme in audio (L139): Aufnahmebefehl, Geräteliste über pactl,
Start/Stopp des Aufnahme-Subprozesses und Bluetooth-Profil-Rückschaltung.
Kein pw-record, kein pactl, kein Gerät — nur Attrappen."""
import os
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import audio


@pytest.fixture
def linux(tmp_path, monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "linux")
    monkeypatch.setattr(audio, "RAW", str(tmp_path / "rec.raw"))
    monkeypatch.setattr(audio, "RAW_B", str(tmp_path / "rec-b.raw"))
    monkeypatch.setattr(audio, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(audio, "_log", lambda m: None)
    monkeypatch.delenv("QUASSEL_PREROLL_MS", raising=False)
    return tmp_path


# ----------------------------------------------------------- record_command
def test_pw_record_is_preferred_and_targets_the_chosen_mic(linux, monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda n: "/usr/bin/" + n)
    cmd = audio.record_command("alsa_input.usb-mic")
    assert cmd[0] == "pw-record"
    assert cmd[cmd.index("--rate") + 1] == "16000"
    assert cmd[cmd.index("--target") + 1] == "alsa_input.usb-mic"
    assert cmd[-1] == "-"
    assert "--target" not in audio.record_command("default")


def test_parecord_is_the_fallback(linux, monkeypatch):
    monkeypatch.setattr(audio.shutil, "which",
                        lambda n: "/usr/bin/parecord" if n == "parecord" else None)
    cmd = audio.record_command("src")
    assert cmd[0] == "parecord"
    assert "--format=s16le" in cmd and "--rate=16000" in cmd
    assert cmd[cmd.index("-d") + 1] == "src"


def test_no_recorder_installed_means_none(linux, monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda n: None)
    assert audio.record_command() is None


# ---------------------------------------------------------------- list_mics
PACTL = """Source #1
\tState: SUSPENDED
\tName: alsa_output.pci.analog-stereo.monitor
\tDescription: Monitor of Built-in Audio
Source #2
\tName: alsa_input.usb-Blue_Yeti
\tDescription: Yeti Stereo Microphone
Source #3
\tName: bluez_input.AA_BB
\tDescription: Headset
"""


def test_list_mics_parses_pactl_and_skips_monitors(linux, monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env", {})
        return SimpleNamespace(stdout=PACTL)
    monkeypatch.setattr(audio.subprocess, "run", run)
    assert audio.list_mics() == [("alsa_input.usb-Blue_Yeti", "Yeti Stereo Microphone"),
                                 ("bluez_input.AA_BB", "Headset")]
    # pactl übersetzt Feldnamen, darum immer LC_ALL=C (KERN: „Nie wieder").
    assert seen["env"].get("LC_ALL") == "C"


def test_list_mics_survives_missing_or_hanging_pactl(linux, monkeypatch):
    def missing(*a, **k):
        raise OSError("no pactl")
    monkeypatch.setattr(audio.subprocess, "run", missing)
    assert audio.list_mics() == []

    def hangs(*a, **k):
        raise subprocess.TimeoutExpired("pactl", 5)
    monkeypatch.setattr(audio.subprocess, "run", hangs)
    assert audio.list_mics() == []


# ----------------------------------------------------- Recorder (Subprozess)
class Proc:
    def __init__(self, hang=False):
        self.signals = []
        self.killed = False
        self.hang = hang

    def poll(self):
        return None

    def send_signal(self, sig):
        self.signals.append(sig)

    def wait(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("pw-record", timeout)
        return 0

    def kill(self):
        self.killed = True


def recorder(monkeypatch, procs, profiles=lambda: {}):
    monkeypatch.setattr(audio, "record_command", lambda mic: ["pw-record", "-"])
    monkeypatch.setattr(audio, "_bluez_profiles", profiles)

    def popen(cmd, stdout=None, stderr=None):
        p = procs.pop(0) if procs else Proc()
        p.stdout_file = stdout
        return p
    monkeypatch.setattr(audio.subprocess, "Popen", popen)
    return audio.Recorder(raw_path=audio.RAW)


def test_recorder_writes_alternating_raw_files_and_stops_with_sigint(linux, monkeypatch):
    p1, p2 = Proc(), Proc()
    rec = recorder(monkeypatch, [p1, p2])
    assert rec.start()
    assert rec.active
    first = rec.stop()
    assert p1.signals == [signal.SIGINT] and not p1.killed
    assert p1.stdout_file.closed
    assert rec.start()
    second = rec.stop()
    assert {first, second} == {audio.RAW, audio.RAW_B}
    assert not rec.active


def test_recorder_kills_a_recorder_that_ignores_sigint(linux, monkeypatch):
    p = Proc(hang=True)
    rec = recorder(monkeypatch, [p])
    assert rec.start()
    rec.stop()
    assert p.signals == [signal.SIGINT] and p.killed


def test_recorder_start_fails_cleanly(linux, monkeypatch):
    monkeypatch.setattr(audio, "record_command", lambda mic: None)
    assert audio.Recorder(raw_path=audio.RAW).start() is False

    def popen(*a, **k):
        raise OSError("pw-record vanished")
    monkeypatch.setattr(audio, "record_command", lambda mic: ["pw-record", "-"])
    monkeypatch.setattr(audio.subprocess, "Popen", popen)
    rec = audio.Recorder(raw_path=audio.RAW)
    assert rec.start() is False
    assert not rec.active


def test_raw_bytes_drops_a_trailing_half_sample(linux):
    rec = audio.Recorder(raw_path=audio.RAW)
    with open(audio.RAW, "wb") as f:
        f.write(b"\1\2\3\4\5")
    assert rec.raw_bytes(audio.RAW) == b"\1\2\3\4"
    assert rec.raw_bytes(str(linux / "missing.raw")) == b""


def test_bluetooth_profile_is_restored_after_the_recording(linux, monkeypatch):
    snapshot = threading.Event()

    def profiles():
        snapshot.set()
        return {"bluez_card.AA": "a2dp-sink"}
    restored = []
    monkeypatch.setattr(audio, "_restore_bluez_profiles", restored.append)
    rec = recorder(monkeypatch, [Proc()], profiles=profiles)
    assert rec.start()
    assert snapshot.wait(2)
    # Der Schnappschuss läuft im Hintergrund; erst wenn er gespeichert ist,
    # gehört er zu dieser Aufnahme.
    for _ in range(200):
        if rec._bt_before:
            break
        threading.Event().wait(0.01)
    rec.stop()
    assert restored == [{"bluez_card.AA": "a2dp-sink"}]


def test_restore_switches_only_changed_cards(monkeypatch):
    calls = []
    monkeypatch.setattr(audio.time, "sleep", lambda s: None)
    monkeypatch.setattr(audio, "_bluez_profiles",
                        lambda: {"bluez_card.A": "headset-head-unit",
                                 "bluez_card.B": "a2dp-sink"})
    monkeypatch.setattr(audio.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd))
    started = []
    real_thread = threading.Thread

    def thread(target=None, daemon=None, **kw):
        t = real_thread(target=target, daemon=daemon, **kw)
        started.append(t)
        return t
    monkeypatch.setattr(audio.threading, "Thread", thread)
    audio._restore_bluez_profiles({"bluez_card.A": "a2dp-sink", "bluez_card.B": "a2dp-sink"})
    started[0].join(2)
    assert calls == [["pactl", "set-card-profile", "bluez_card.A", "a2dp-sink"]]
