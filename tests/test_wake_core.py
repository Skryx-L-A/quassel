"""Wake-Word im gemeinsamen Diktat-Kern: Aufnahme mit Spotter/VAD-Klassifikator,
Einfuege-Sperre, Auto-Stopp in den Hauptschleifen. Ohne Mikrofon, Server, Modell."""
import os
import struct
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from quassel import config, dictation, vad, wakeword

RATE = 16000


def pcm(amplitude, seconds):
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *([amplitude] * n))


class FakeRec:
    def __init__(self, audio, raw_path):
        self._audio, self.raw_path = audio, raw_path
        self.data = b""
        self.stopped = False

    def start(self, mic):
        with open(self.raw_path, "wb") as f:
            f.write(b"x")
        return True

    def raw_bytes(self):
        return self.data

    def stop(self):
        self.stopped = True

    def tick(self):
        self.data += self._audio[len(self.data):len(self.data) + 3200]


def make_core(tmp_path, monkeypatch, audio, **cfg):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))
    core = dictation.DictationCore.__new__(dictation.DictationCore)
    core.cfg = SimpleNamespace(mic="default", vad_backend="rms", **{"beep": False, **cfg})
    core.wake = object()
    core.logs = []
    core._log = core.logs.append
    core.states = []
    core._state = lambda state, text="": core.states.append((state, text))
    core._start_level = core._stop_level = lambda: None
    core.rec = SimpleNamespace(active=False)
    rec = FakeRec(audio, str(tmp_path / "wake.raw"))
    core._new_recorder = lambda path: rec

    clock = {"now": 0.0}
    real = wakeword.capture_utterance

    def fast(*a, **k):
        def sleep(dt):
            clock["now"] += dt
            rec.tick()
        return real(*a, sleep=sleep, clock=lambda: clock["now"], **k)

    monkeypatch.setattr(wakeword, "capture_utterance", fast)
    return core, rec


def test_wake_recording_uses_the_shared_classifier_and_removes_the_raw_file(
        tmp_path, monkeypatch):
    core, rec = make_core(tmp_path, monkeypatch, pcm(0, 0.5) + pcm(5000, 1.0) + pcm(0, 3.0))
    out = core._wake_record_utterance()
    assert out is not None and len(out) > 16000
    assert rec.stopped
    assert not os.path.exists(rec.raw_path)         # Raum-Audio nicht liegen lassen


def test_wake_recording_without_speech_gives_nothing(tmp_path, monkeypatch):
    core, rec = make_core(tmp_path, monkeypatch, pcm(0, 14.0))
    assert core._wake_record_utterance() is None
    assert not os.path.exists(rec.raw_path)


def test_wake_recording_waits_for_the_spotter_and_followup_does_not(tmp_path, monkeypatch):
    class Spotter:
        chunk_samples = wakeword.SPOTTER_CHUNK

        def __init__(self):
            self.n = 0

        def reset(self):
            pass

        def feed(self, chunk):
            self.n += 1
            return self.n == 5

    spotter = Spotter()
    monkeypatch.setattr(wakeword, "load_spotter", lambda cfg, log=None: spotter)
    audio = pcm(4000, 0.8) + pcm(3000, 0.5) + pcm(0, 3.0)
    core, rec = make_core(tmp_path, monkeypatch, audio)
    assert core._wake_record_utterance() is not None and spotter.n >= 5
    spotter.n = 0
    rec.data = b""
    assert core._wake_record_followup() is not None
    assert spotter.n == 0, "die Folgeaufnahme laeuft ohne Spotter"
    assert core.states[0] == ("recording", dictation.tr("wake_listening"))
    assert core.states[-1][0] == "idle"


def test_wake_busy_covers_the_whole_dictation_not_only_the_recording(tmp_path, monkeypatch):
    core, _ = make_core(tmp_path, monkeypatch, b"")
    core._runner = SimpleNamespace(current_action=None)
    core._sync_action = None
    assert core._wake_busy() is False
    core._runner.current_action = "finish"          # Rest laeuft: Transkription/Einfuegen
    assert core._wake_busy() is True
    core._runner.current_action = None
    core.rec.active = True
    assert core._wake_busy() is True


def test_keyboard_insert_takes_turns_with_a_wake_insert(tmp_path, monkeypatch):
    core, _ = make_core(tmp_path, monkeypatch, b"")
    order = []
    core._paste = lambda text: order.append("keyboard") or True
    gate = wakeword.INSERT_GATE
    assert gate.enter_wake()
    t = threading.Thread(target=lambda: core._insert("hallo"))
    t.start()
    time.sleep(0.1)
    assert order == []                              # wartet auf das Wake-Einfuegen
    order.append("wake done")
    gate.exit_wake()
    t.join(2)
    assert order == ["wake done", "keyboard"]
    # und der Wake-Thread selbst darf durch _insert hindurch (teilt den Pfad)
    assert gate.enter_wake()
    assert core._insert("x") is True
    gate.exit_wake()


def test_wake_listener_is_built_with_followup_and_trust(tmp_path, monkeypatch):
    core, _ = make_core(tmp_path, monkeypatch, b"", wakeword_enabled=True,
                        wakeword_spotter_model="/x/m.onnx",
                        wakeword_spotter_threshold=0.5, wakeword_verify=True)
    core.wake = None
    started = []
    monkeypatch.setattr(wakeword.WakeListener, "start", lambda self: started.append(self))
    core._wake_transcribe = core._wake_insert = lambda *a: None
    core._sync_wakeword()
    [listener] = started
    assert listener._trust_capture is True
    assert listener._record_followup == core._wake_record_followup


# -------------------------------------------------- Auto-Stopp in den Hauptschleifen
def test_linux_loop_ends_a_handsfree_recording_after_silence():
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.rec = SimpleNamespace(active=True, started=100.0)
    d.autostop_due = lambda: True
    assert d._record_expired("toggle", 101.0) and d._record_expired("toggle_armed", 101.0)
    assert not d._record_expired("hold", 101.0)     # Halten-Modus: nie Auto-Stopp
    d.autostop_due = lambda: False
    assert not d._record_expired("toggle", 101.0)


def test_mac_watchdog_finishes_handsfree_after_silence():
    from quassel.daemon import Daemon
    d = Daemon.__new__(Daemon)
    d.rec = SimpleNamespace(active=True, started=100.0)
    calls = []
    now = 105.0
    listener = SimpleNamespace(force_finish=lambda: calls.append(1) or True,
                               last_event_tick=now, last_action_tick=now,
                               current_action=None, current_sync=None,
                               machine=SimpleNamespace(state="toggle"))
    d.autostop_due = lambda: False
    d._mac_watchdog(listener, now=now)
    assert calls == []
    d.autostop_due = lambda: True
    d._mac_watchdog(listener, now=now)
    assert calls == [1]


def test_windows_wake_busy_includes_a_running_action():
    import test_win_stubs as win_stubs
    mp = __import__("pytest").MonkeyPatch()
    try:
        app = win_stubs.load_win_app(mp)
        core = app.WinDictation.__new__(app.WinDictation)
        core.rec = SimpleNamespace(active=False)
        core.owner = SimpleNamespace(enabled=True)
        core._sync_action = None
        core._runner = SimpleNamespace(current_action=None)
        assert core._wake_busy() is False
        core._runner.current_action = "finish"
        assert core._wake_busy() is True
        core._runner.current_action = None
        core.owner.enabled = False
        assert core._wake_busy() is True
    finally:
        mp.undo()
