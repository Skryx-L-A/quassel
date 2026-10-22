"""Parität: Linux/macOS-Daemon und Windows-App machen dieselben Schritte in
derselben Reihenfolge (Audit E72).

Beide laufen über dictation.DictationCore; dieser Test prüft, dass auch die
Plattform-Anbindung jeden Schritt gleich weiterreicht. Jede Plattform bekommt
dieselben Attrappen für Aufnahme, Server und Einfügen; mitgeschrieben wird,
was nach außen geht (Zustand, Ton, WAV, Einfügen, Tasten)."""
import os
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from quassel import daemon as daemon_mod
from quassel import dictation
import test_win_stubs as win_stubs


class FakeRec:
    def __init__(self, data):
        self.data = data
        self.active = False
        self.started = 0.0
        self.stream_abandoned = False
        self.path = None
        self.n = 0

    def start(self, mic="default"):
        self.n += 1
        self.path = "rec.raw" if self.n % 2 else "rec-b.raw"
        self.active = True
        return True

    def stop(self):
        self.active = False
        return self.path

    def raw_bytes(self, path=None):
        return self.data


class NoPartial:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def cfg(**extra):
    base = dict(beep=True, beep_output="system", mic="default", mute_mode="music",
                ui_language="auto", streaming=False, ai_enabled=False,
                programmer_mode=False, text_replace=False, history_enabled=False,
                stats_enabled=False, auto_learn=False, pill_preview=False,
                reload=lambda: False)
    base.update(extra)
    return SimpleNamespace(**base)


def common(monkeypatch, steps, tmp_path, server_up, transcript, kind):
    monkeypatch.setattr(dictation.beep, "start", lambda: steps.append(("beep", "start")))
    monkeypatch.setattr(dictation.beep, "stop", lambda: steps.append(("beep", "stop")))
    monkeypatch.setattr(dictation.beep, "set_output", lambda name: None)
    monkeypatch.setattr(dictation.whisperclient, "server_was_up", lambda: True)
    monkeypatch.setattr(dictation.whisperclient, "ensure_server",
                        lambda deadline=600: steps.append(("server",)) or server_up)
    monkeypatch.setattr(dictation.whisperclient, "transcribe",
                        lambda *a, **k: transcript)
    monkeypatch.setattr(dictation.textproc, "postprocess",
                        lambda raw, c: (kind, transcript))
    monkeypatch.setattr(dictation, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(dictation, "TAIL_PAD_MS", 0)
    monkeypatch.setattr(dictation, "PartialLoop", NoPartial)


def ducker(steps):
    return SimpleNamespace(apply=lambda mode: steps.append(("duck", mode)),
                           restore=lambda: steps.append(("unduck",)))


def wav_writer(steps):
    def wav(data, path):
        steps.append(("wav", os.path.basename(path).split("-")[0]))
    return wav


def run_daemon(monkeypatch, tmp_path, scenario):
    steps = []
    common(monkeypatch, steps, tmp_path, **scenario["env"])
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.cfg = cfg()
    d.rec = FakeRec(scenario["audio"])
    d.ducker = ducker(steps)
    monkeypatch.setattr(daemon_mod, "TAIL_PAD_MS", 0)
    monkeypatch.setattr(daemon_mod, "PartialLoop", NoPartial)
    monkeypatch.setattr(daemon_mod, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(daemon_mod, "log", lambda m: None)
    monkeypatch.setattr(daemon_mod, "mic_is_bluetooth", lambda mic: False)
    monkeypatch.setattr(daemon_mod.config, "read_serverenv", lambda: {"VAD_MODEL": "x"})
    monkeypatch.setattr(daemon_mod, "state_set",
                        lambda s, text="": steps.append(("state", s, text)))
    monkeypatch.setattr(daemon_mod, "notify", lambda text, ms=4000: None)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", wav_writer(steps))
    monkeypatch.setattr(daemon_mod, "paste", lambda t: steps.append(("paste", t)))
    monkeypatch.setattr(daemon_mod, "send_enter", lambda: steps.append(("enter",)))
    monkeypatch.setattr(daemon_mod, "send_backspaces",
                        lambda n: steps.append(("backspace", n)))
    scenario["drive"](d)
    return steps


def run_windows(monkeypatch, tmp_path, scenario):
    steps = []
    app = win_stubs.load_win_app(monkeypatch)
    common(monkeypatch, steps, tmp_path, **scenario["env"])
    monkeypatch.setattr(app, "dlog", lambda m: None)
    monkeypatch.setattr(app.server, "vad_model_path", lambda: "vad.bin")
    monkeypatch.setattr(app, "wav_from_raw", wav_writer(steps))
    monkeypatch.setattr(app, "paste", lambda t: steps.append(("paste", t)))
    monkeypatch.setattr(app, "send_enter", lambda: steps.append(("enter",)))
    monkeypatch.setattr(app, "send_backspaces", lambda n: steps.append(("backspace", n)))

    def emit(state, text):
        # Windows nennt den Ruhezustand "ready" (Pille); inhaltlich "idle".
        steps.append(("state", "idle" if state == "ready" else state, text))
    w = app.WinDictation.__new__(app.WinDictation)
    w.owner = SimpleNamespace(enabled=True, pump=None,
                              sig_state=SimpleNamespace(emit=emit))
    w.cfg = cfg()
    w.rec = FakeRec(scenario["audio"])
    w.ducker = ducker(steps)
    scenario["drive"](w)
    return steps


def dictate(core):
    assert core.start_recording()
    core.finish_now()


def dictate_then_panic(core):
    assert core.start_recording()
    rest = core.finish()
    # Der Rest läuft schon auf dem Aktions-Thread, als der Not-Aus kommt.
    core._runner = SimpleNamespace(current_action="finish", abandon=lambda: True)
    core.panic_stop()
    rest()


def cancel(core):
    assert core.start_recording()
    core.cancel_recording("canceled_key")


AUDIO = b"\x01\x00" * 20000
SCENARIOS = {
    "text": dict(audio=AUDIO, drive=dictate,
                 env=dict(server_up=True, transcript="hallo welt", kind="text")),
    "server_down_rescue": dict(audio=AUDIO, drive=dictate,
                               env=dict(server_up=False, transcript=None, kind="text")),
    "enter_command": dict(audio=AUDIO, drive=dictate,
                          env=dict(server_up=True, transcript="enter", kind="command")),
    "too_short": dict(audio=b"\x01\x00" * 100, drive=dictate,
                      env=dict(server_up=True, transcript="x", kind="text")),
    "panic": dict(audio=AUDIO, drive=dictate_then_panic,
                  env=dict(server_up=True, transcript="hallo", kind="text")),
    "cancel": dict(audio=AUDIO, drive=cancel,
                   env=dict(server_up=True, transcript="x", kind="text")),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_daemon_and_windows_take_the_same_steps(monkeypatch, tmp_path, name):
    scenario = SCENARIOS[name]
    linux = run_daemon(monkeypatch, tmp_path / "d", scenario)
    windows = run_windows(monkeypatch, tmp_path / "w", scenario)

    def norm(steps):
        # Der Rettungspfad nennt den eigenen tmp-Ordner und die Sekunde der
        # Rettung; beide Läufe können eine Sekundengrenze überspannen.
        def text(t):
            t = t.replace(str(tmp_path / "d"), "<run>").replace(str(tmp_path / "w"), "<run>")
            return re.sub(r"rescued-\d{8}-\d{6}", "rescued-<zeit>", t)
        return [s[:2] + (text(s[2]),) if s[0] == "state" and len(s) == 3 else s
                for s in steps]
    assert norm(linux) == norm(windows)
    assert linux, "Szenario hat nichts ausgelöst"


def test_the_scenarios_cover_the_expected_paths(monkeypatch, tmp_path):
    """Gegenprobe, damit der Paritätstest nicht über leere Abläufe grün wird."""
    steps = run_daemon(monkeypatch, tmp_path, SCENARIOS["text"])
    assert steps[:3] == [("beep", "start"), ("duck", "music"), ("state", "recording", "")]
    assert ("paste", "hallo welt") in steps
    assert steps[-1] == ("state", "done", "hallo welt")
    rescue = run_daemon(monkeypatch, tmp_path, SCENARIOS["server_down_rescue"])
    assert rescue[-1][1] == "error" and "rescued-" in rescue[-1][2]
    panic = run_daemon(monkeypatch, tmp_path, SCENARIOS["panic"])
    assert not any(s[0] == "paste" for s in panic)
