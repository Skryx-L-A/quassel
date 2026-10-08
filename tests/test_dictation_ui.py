"""Was die Oberfläche vom gemeinsamen Diktatkern mitbekommt: Mikrofonpegel
(H112), wann eine Systembenachrichtigung kommt (H118) und welche
Fehlermeldung (H113). Ohne Mikrofon, Server, Tastatur und Fenster."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from quassel import daemon as daemon_mod
from quassel import dictation, i18n


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


class FakeRec:
    def __init__(self, events, start_ok=True, data=b"\1\0" * 16000):
        self.events = events
        self.start_ok = start_ok
        self.data = data
        self.active = False

    def start(self, mic):
        self.active = self.start_ok
        return self.start_ok

    def stop(self):
        self.events.append("rec.stop")
        self.active = False
        return None

    def raw_bytes(self, path=None):
        return self.data


class FakePump:
    def __init__(self, events, target):
        self.events = events
        self.target = target

    def start(self):
        self.events.append("pump.start")
        return self

    def stop(self):
        self.events.append("pump.stop")


class Core(dictation.DictationCore):
    def __init__(self, target=None, pill=True, **rec):
        self.events = []
        self.states, self.notes = [], []
        self.cfg = SimpleNamespace(beep=False, mic="default", mute_mode="off",
                                   streaming=False, pill_enabled=pill)
        self.rec = FakeRec(self.events, **rec)
        self.ducker = SimpleNamespace(apply=lambda m: None, restore=lambda: None)
        self._target = target

    def _refresh_cfg(self):
        pass

    def _new_partial(self):
        return SimpleNamespace(start=lambda: None, stop=lambda: None)

    def _level_target(self):
        return self._target

    def _state(self, state, text=""):
        self.states.append((state, text))

    def _notify(self, text):
        self.notes.append(text)

    def _log(self, msg):
        pass


@pytest.fixture
def pumps(monkeypatch):
    made = []

    def factory(target):
        pump = FakePump(made_events, target)
        made.append(pump)
        return pump
    made_events = []
    monkeypatch.setattr(dictation, "LevelPump", factory)
    monkeypatch.setattr(dictation.time, "sleep", lambda s: None)
    return made, made_events


# ---------------------------------------------------------------- H112 Pegel
def test_recording_starts_a_level_pump_with_the_platform_target(pumps):
    made, events = pumps
    target = object()
    core = Core(target=target)
    assert core.start_recording() is True
    assert len(made) == 1 and made[0].target is target
    assert events == ["pump.start"]


def test_without_a_target_no_pump_runs(pumps):
    made, _ = pumps
    Core(target=None).start_recording()
    assert made == []


def test_finish_stops_the_pump_before_the_recorder(pumps):
    made, events = pumps
    core = Core(target=lambda v: None)
    core.start_recording()
    core.rec.events = events
    core._finish_capture()
    assert events == ["pump.start", "pump.stop", "rec.stop"]
    assert core._level_pump is None


@pytest.mark.parametrize("how", ["cancel", "panic"])
def test_cancel_and_panic_stop_the_pump(pumps, how):
    made, events = pumps
    core = Core(target=lambda v: None)
    core.start_recording()
    core.rec.events = events
    if how == "cancel":
        core.cancel_recording("canceled_key")
    else:
        core.panic_stop()
    assert events[:3] == ["pump.start", "pump.stop", "rec.stop"]


def test_a_broken_pump_never_stops_the_dictation(monkeypatch):
    def broken(target):
        raise RuntimeError("kein Thread")
    monkeypatch.setattr(dictation, "LevelPump", broken)
    core = Core(target=lambda v: None)
    assert core.start_recording() is True
    assert core.states[-1][0] == "recording"


def test_daemon_sends_the_level_through_state_json(monkeypatch):
    got = []
    monkeypatch.setattr(daemon_mod, "level_set", got.append)
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d._level_target()(0.25)
    assert got == [0.25]
