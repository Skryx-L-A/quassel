"""Linux microphone listing without querying a real PulseAudio server."""
from types import SimpleNamespace

from quassel import audio


def test_list_mics_uses_stable_pactl_locale(monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "linux")
    monkeypatch.setattr(audio, "IS_MAC", False)
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["env"]["LC_ALL"] == "C"
        return SimpleNamespace(stdout=(
            "Name: alsa_input.microphone\n"
            "Description: USB Microphone\n"
            "Name: alsa_output.monitor\n"
            "Description: Monitor\n"))

    monkeypatch.setattr(audio.subprocess, "run", run)
    assert audio.list_mics() == [("alsa_input.microphone", "USB Microphone")]
    assert calls[0][0] == ["pactl", "list", "sources"]
