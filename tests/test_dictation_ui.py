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


# ------------------------------------------- H118 weniger Benachrichtigungen
def test_other_key_cancel_is_silent(pumps):
    core = Core()
    core.start_recording()
    core.cancel_recording("canceled_key")
    assert core.notes == []
    assert core.states[-1] == ("idle", "")


def test_single_tap_goes_to_the_pill_not_the_system(pumps):
    core = Core()
    core.start_recording()
    core.cancel_recording("canceled_tap")
    assert core.notes == []
    assert core.states[-1] == ("done", i18n.tr("canceled_tap"))


def test_too_short_goes_to_the_pill(pumps):
    core = Core(data=b"\1\0" * 10)
    core.start_recording()
    assert core._finish_capture() is None
    assert core.notes == []
    assert core.states[-1] == ("done", i18n.tr("too_short"))


def test_without_a_pill_the_system_notification_remains(pumps):
    core = Core(pill=False)
    core.start_recording()
    core.cancel_recording("canceled_tap")
    assert core.notes == [i18n.tr("canceled_tap")]
    assert core.states[-1] == ("idle", "")


def test_panic_still_notifies(pumps):
    """Der Not-Aus ist eine bewusste Handlung, oft bei hängendem Hotkey: die
    Bestätigung bleibt eine Systembenachrichtigung."""
    core = Core()
    core.start_recording()
    core.panic_stop()
    assert core.notes == [i18n.tr("panic_stopped")]


# ------------------------------------------- H113 konkrete Fehlermeldungen
from quassel.backends import SpeechResult  # noqa: E402


@pytest.mark.parametrize("error,key", [("timeout", "err_timeout"),
                                       ("conn", "err_server_down"),
                                       ("http", "err_transcribe"),
                                       ("io", "err_transcribe"),
                                       (None, "err_transcribe")])
def test_failed_transcription_names_the_cause(error, key):
    assert dictation.failure_text(SpeechResult(text=None, error=error)) == i18n.tr(key)


def test_server_not_starting_names_a_missing_model(monkeypatch):
    monkeypatch.setattr(dictation.whisperclient, "missing_model", lambda: "ggml-large.bin")
    assert dictation.failure_text(None) == i18n.tr("err_model_missing", model="ggml-large.bin")
    monkeypatch.setattr(dictation.whisperclient, "missing_model", lambda: None)
    assert dictation.failure_text(None) == i18n.tr("err_server_down")


class RescueCore(Core):
    def _rescue(self, data):
        return "/run/rescued-1.wav"


def test_rescue_message_says_what_went_wrong(monkeypatch):
    core = RescueCore()
    core._fail_with_rescue(b"x", None, None, SpeechResult(text=None, error="timeout"))
    text = core.states[-1][1]
    assert text.startswith(i18n.tr("err_timeout"))
    assert "rescued-1.wav" in text
    assert i18n.tr("no_server") not in text


def test_failed_microphone_start_reports_in_pill_and_notification():
    core = Core(start_ok=False)
    assert core.start_recording() is False
    assert core.states == [("error", i18n.tr("err_mic_open"))]
    assert core.notes == [i18n.tr("err_mic_open")]


# ------------------------------------------- Rohtext zurück (Textbereinigung)
class ClipCore(Core):
    def __init__(self, ok=True):
        super().__init__()
        self.clips = []
        self._ok = ok

    def _clip_copy(self, text):
        self.clips.append(text)
        return self._ok


def test_raw_back_copies_the_raw_text_and_never_names_it():
    core = ClipCore()
    core._last_raw = "äh also das ist der Rohtext"
    assert core.raw_back() is True
    assert core.clips == ["äh also das ist der Rohtext"]
    state, text = core.states[-1]
    assert state == "done" and "Rohtext" not in text and "clipboard" in text


def test_raw_back_without_raw_text():
    core = ClipCore()
    assert core.raw_back() is False
    assert core.clips == [] and core.states[-1] == ("done", i18n.tr("raw_back_none"))


def test_raw_back_clipboard_failure_is_reported():
    core = ClipCore(ok=False)
    core._last_raw = "x"
    assert core.raw_back() is False
    assert core.states[-1] == ("error", i18n.tr("raw_back_failed"))


def test_daemon_raw_back_signal_and_clipboard(monkeypatch):
    got = []
    monkeypatch.setattr(daemon_mod, "clip_copy", got.append)
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d._clip_copy("roh")
    assert got == ["roh"]


@pytest.mark.parametrize("active,cleaned,kept", [(True, "Hallo.", "äh hallo"),
                                                 (True, "äh hallo", None),
                                                 (False, "Hallo.", None)])
def test_raw_text_is_kept_in_memory_only_when_cleanup_changed_it(monkeypatch, active,
                                                                 cleaned, kept):
    core = ClipCore()
    core._last_raw = "vom Diktat davor"
    monkeypatch.setattr(dictation, "postprocess_transcript",
                        lambda raw, cfg, *a: ("text", "äh hallo"))
    core._refine = lambda text: cleaned
    core._cleanup_active = lambda: active
    core._insert = lambda text: True
    core._forget_text = lambda token: None
    core._after_insert = lambda value, seconds=0.0: None
    core._wake_insert_with(core.cfg, "äh hallo")
    assert core._last_raw == kept
