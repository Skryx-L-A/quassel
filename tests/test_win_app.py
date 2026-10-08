"""Windows-App auf der gemeinsamen Ablaufsteuerung (Audit E72, B26, B27, B28).

quassel/win/app.py wird mit Attrappen für Hook und Einfügen geladen (siehe
test_win_stubs.py). Aufnahme über den echten Windows-Recorder mit einem
sounddevice-Ersatz; alle Dateien liegen in tmp_path."""
import os
import queue
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from quassel import dictation
from quassel.win import audio_win
import test_win_stubs as win_stubs
from test_win_audio import FakeSD

VK_CTRL, VK_WIN, VK_A = 0xA2, 0x5B, 0x41


class NoPartial:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def make_cfg(**extra):
    cfg = dict(beep=False, beep_output="system", mic="default", mute_mode="off",
               ui_language="auto", streaming=False, ai_enabled=False,
               programmer_mode=False, text_replace=False, history_enabled=False,
               stats_enabled=False, auto_learn=False, pill_preview=False,
               wakeword_phrase="Hey Quassel", reload=lambda: False)
    cfg.update(extra)
    return SimpleNamespace(**cfg)


@pytest.fixture
def win(monkeypatch, tmp_path):
    """WinDictation ohne Qt-Besitzer: Zustände, Log und Einfügen landen in seen."""
    app = win_stubs.load_win_app(monkeypatch)
    seen = {"states": [], "logs": [], "wavs": []}
    sd = FakeSD()
    monkeypatch.setattr(audio_win, "sd", sd)
    monkeypatch.setattr(audio_win, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(audio_win, "RAW", str(tmp_path / "rec.raw"))
    monkeypatch.setattr(audio_win, "RAW_B", str(tmp_path / "rec-b.raw"))
    monkeypatch.setattr(dictation, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(dictation, "WAV", str(tmp_path / "rec.wav"))
    monkeypatch.setattr(dictation, "WAKE_RAW", str(tmp_path / "wake.raw"))
    monkeypatch.setattr(dictation, "TAIL_PAD_MS", 0)
    monkeypatch.setattr(dictation, "PartialLoop", NoPartial)
    monkeypatch.setattr(dictation.whisperclient, "server_was_up", lambda: True)
    monkeypatch.setattr(dictation.beep, "set_output", lambda name: None)
    monkeypatch.setattr(app, "dlog", seen["logs"].append)
    monkeypatch.setattr(app.server, "vad_model_path", lambda: None)

    def wav(data, path):
        seen["wavs"].append((path, bytes(data)))
        with open(path, "wb") as f:
            f.write(data)
    monkeypatch.setattr(app, "wav_from_raw", wav)
    owner = SimpleNamespace(
        enabled=True, pump=None,
        sig_state=SimpleNamespace(emit=lambda s, t: seen["states"].append((s, t))))
    core = app.WinDictation.__new__(app.WinDictation)
    core.owner = owner
    core.cfg = make_cfg()
    core.rec = audio_win.Recorder()
    core.ducker = SimpleNamespace(apply=lambda mode: None, restore=lambda: None)
    return SimpleNamespace(app=app, core=core, seen=seen, sd=sd, tmp=tmp_path)


def test_unreachable_server_rescues_the_recording_on_windows(win, monkeypatch):
    """B28: der CHANGELOG verspricht die Rettung auch für Windows."""
    monkeypatch.setattr(dictation.whisperclient, "ensure_server",
                        lambda deadline=600: False)
    assert win.core.start_recording()
    win.sd.streams[0].feed(b"\x01\x00" * 20000)
    win.core.finish_now()
    rescued = list(win.tmp.glob("rescued-*.wav"))
    assert len(rescued) == 1
    state, text = win.seen["states"][-1]
    assert state == "error" and str(rescued[0]) in text


def test_failed_transcription_rescues_the_recording_on_windows(win, monkeypatch):
    monkeypatch.setattr(dictation.whisperclient, "ensure_server",
                        lambda deadline=600: True)
    monkeypatch.setattr(dictation.whisperclient, "transcribe", lambda *a, **k: None)
    assert win.core.start_recording()
    win.sd.streams[0].feed(b"\x01\x00" * 20000)
    win.core.finish_now()
    assert len(list(win.tmp.glob("rescued-*.wav"))) == 1
    assert win.seen["states"][-1][0] == "error"


def test_wake_listener_records_into_its_own_files(win, monkeypatch):
    """B26: Wake-Word und Diktat teilen weder Rohdatei noch WAV."""
    wake_rec = win.core._new_recorder(dictation.WAKE_RAW)
    assert wake_rec.raw_path == dictation.WAKE_RAW
    assert wake_rec.raw_path not in (audio_win.RAW, audio_win.RAW_B)
    monkeypatch.setattr(dictation.whisperclient, "ensure_server", lambda: True)
    monkeypatch.setattr(dictation.whisperclient, "transcribe", lambda *a, **k: "hallo")
    assert win.core._wake_transcribe(b"\x01\x00" * 8000) == "hallo"
    [wake_wav] = [path for path, _ in win.seen["wavs"]]
    assert os.path.basename(wake_wav).startswith("wake-")
    assert wake_wav != dictation.scratch_wav("rec")
    assert not os.path.exists(wake_wav)          # A6: nach der Erkennung gelöscht


class FakeRunner:
    def __init__(self):
        self.submitted = []
        self.current_action = None

    def submit(self, name, fn):
        self.submitted.append((name, fn))


def test_new_dictation_in_the_tail_reads_the_right_audio(win, monkeypatch):
    """B27: ein Chord direkt nach dem Loslassen startet das nächste Diktat.
    Der Ereignis-Thread hat die erste Aufnahme dann schon gestoppt und
    gelesen; transkribiert wird ihr Ton, nicht der des neuen Diktats."""
    events, runner = queue.Queue(), FakeRunner()
    pump = dictation.ChordPump(events, win.core, runner, {VK_CTRL}, {VK_WIN},
                               hold_min=0.3, double_window=0.3)
    t = 100.0
    events.put((VK_CTRL, True))
    pump.step(timeout=0, now=t)
    events.put((VK_WIN, True))
    pump.step(timeout=0, now=t)                    # Chord -> Aufnahme 1
    win.sd.streams[0].feed(b"\x01\x00" * 20000)
    events.put((VK_WIN, False))
    pump.step(timeout=0, now=t + 1)
    events.put((VK_CTRL, False))
    pump.step(timeout=0, now=t + 1)                # losgelassen -> finish synchron
    assert [name for name, _ in runner.submitted] == ["finish"]
    events.put((VK_CTRL, True))
    pump.step(timeout=0, now=t + 1.05)
    events.put((VK_WIN, True))
    pump.step(timeout=0, now=t + 1.05)             # Aufnahme 2 im Nachlauf
    assert win.core.rec.active
    win.sd.streams[1].feed(b"\x02\x00" * 20000)

    monkeypatch.setattr(dictation.whisperclient, "ensure_server",
                        lambda deadline=600: True)
    monkeypatch.setattr(dictation.whisperclient, "transcribe",
                        lambda *a, **k: "hallo welt")
    monkeypatch.setattr(dictation.textproc, "postprocess",
                        lambda raw, cfg: ("text", "hallo welt"))
    runner.submitted[0][1]()                       # Rest des ersten Diktats
    path, data = win.seen["wavs"][-1]
    assert set(data[::2]) == {1}                   # Ton von Aufnahme 1
    assert win.core.rec.active                     # Aufnahme 2 läuft unberührt weiter
    assert ("paste", "hallo welt") in win_stubs.CALLS


def test_idle_state_maps_to_ready_or_off(win):
    win.core._state("idle")
    win.core.owner.enabled = False
    win.core._state("idle")
    assert win.seen["states"] == [("ready", ""), ("off", "")]


def test_failed_microphone_shows_an_error_and_does_not_arm(win, monkeypatch):
    monkeypatch.setattr(audio_win, "sd", FakeSD(fail=True))
    assert win.core.start_recording() is False
    assert win.seen["states"][-1][0] == "error"


def test_windows_uses_the_shared_flow_and_not_a_copy(win):
    """E72: keine zweite Fassung der Diktatlogik in win/app.py."""
    app = win.app
    assert issubclass(app.WinDictation, dictation.DictationCore)
    for name in ("PartialWorker", "postprocess_transcript"):
        assert not hasattr(app, name), name
    for name in ("_finish_capture", "_transcribe_and_insert", "_fail_with_rescue",
                 "_after_insert", "_ai_refine", "_wake_record_utterance"):
        assert name not in vars(app.WinDictation), name
