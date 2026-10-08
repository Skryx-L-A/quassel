"""Auto-Stopp nach Stille im Freihand-Modus (Audit #19): Schalter aus, nur nach
erkannter Sprache, einstellbare Dauer, Anbindung an den Ereignis-Thread."""
import os
import queue
import struct
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import config, dictation, vad
from quassel.vad import AutoStop, RmsClassifier, SilenceDetector

RATE = 16000


def pcm(amplitude, seconds):
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *([amplitude] * n))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Source:
    """Wachsende Aufnahme: read_from(offset) liefert nur das Neue."""

    def __init__(self):
        self.data = b""

    def read_from(self, offset):
        return self.data[offset:]


def make(silence=1.5, **kw):
    clock, src = Clock(), Source()
    det = SilenceDetector(rate=RATE, classifier=RmsClassifier(threshold=350.0),
                          hang_sec=silence, min_speech_sec=0.3)
    return AutoStop(det, src.read_from, poll_sec=0.2, clock=clock, **kw), src, clock


def step(stop, src, clock, audio, dt):
    src.data += audio
    clock.now += dt
    return stop.due()


# ------------------------------------------------------------------ AutoStop
def test_stops_after_speech_and_the_configured_silence():
    stop, src, clock = make(silence=1.5)
    assert not step(stop, src, clock, pcm(8000, 1.0), 1.0)
    assert not step(stop, src, clock, pcm(0, 1.0), 1.0)      # erst 1,0 s Stille
    assert step(stop, src, clock, pcm(0, 0.6), 0.6)           # 1,6 s -> Stopp


def test_never_stops_without_speech():
    stop, src, clock = make(silence=1.5)
    for _ in range(20):
        assert not step(stop, src, clock, pcm(0, 1.0), 1.0)


def test_noise_clicks_without_speech_do_not_arm_it():
    stop, src, clock = make(silence=0.5)
    for _ in range(10):
        step(stop, src, clock, pcm(9000, 0.03), 0.3)
        assert not step(stop, src, clock, pcm(0, 1.0), 1.0)


def test_polling_is_throttled():
    stop, src, clock = make()
    reads = []
    inner = src.read_from
    stop._read_from = lambda off: reads.append(off) or inner(off)
    for _ in range(10):
        stop.due()                          # Uhr steht: nur der erste liest
    assert len(reads) == 1
    clock.now += 0.25
    stop.due()
    assert len(reads) == 2


def test_reads_only_the_new_bytes():
    stop, src, clock = make()
    offsets = []
    inner = src.read_from
    stop._read_from = lambda off: offsets.append(off) or inner(off)
    step(stop, src, clock, pcm(8000, 0.5), 0.5)
    step(stop, src, clock, pcm(8000, 0.25), 0.5)
    assert offsets == [0, int(RATE * 0.5) * 2]


def test_a_read_error_turns_it_off_without_raising():
    stop, src, clock = make()

    def boom(off):
        raise OSError("weg")

    stop._read_from = boom
    clock.now += 1
    assert stop.due() is False
    assert isinstance(stop.error, OSError)
    clock.now += 1
    assert stop.due() is False


def test_odd_byte_chunks_stay_aligned():
    stop, src, clock = make(silence=0.6)
    audio = pcm(8000, 0.6) + pcm(0, 0.7)
    for end in range(333, len(audio) + 333, 333):
        src.data = audio[:end]
        clock.now += 0.3
        stop.due()
    assert stop.detector.stopped


# ----------------------------------------------------------- Schalter und Dauer
def ini(tmp_path, monkeypatch, text):
    path = tmp_path / "config.ini"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG", str(path))


def test_switch_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))
    assert vad.make_autostop(SimpleNamespace(), lambda off: b"") is None


def test_switch_on_uses_the_default_and_the_configured_duration(tmp_path, monkeypatch):
    ini(tmp_path, monkeypatch, "[vad]\nautostop = true\nbackend = rms\n")
    stop = vad.make_autostop(SimpleNamespace(), lambda off: b"")
    assert stop.detector.hang_sec == 1.5
    ini(tmp_path, monkeypatch, "[vad]\nautostop = true\nautostop_silence = 2.5\nbackend = rms\n")
    assert vad.make_autostop(SimpleNamespace(), lambda off: b"").detector.hang_sec == 2.5


# --------------------------------------------------------- Anbindung im Diktat-Kern
class FakeRec:
    def __init__(self, path):
        self.active = True
        self.running_path = path
        self.raw_path = path
        self.started = 0.0


def make_core(tmp_path, monkeypatch, enabled=True, silence=0.5):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "fehlt.ini"))
    raw = tmp_path / "rec.raw"
    raw.write_bytes(b"")
    cfg = SimpleNamespace(vad_autostop=enabled, vad_autostop_silence=silence,
                          vad_backend="rms", streaming=False)
    core = dictation.DictationCore.__new__(dictation.DictationCore)
    core.cfg, core.rec = cfg, FakeRec(str(raw))
    core.logs = []
    core._log = core.logs.append
    return core, raw


def append(raw, audio):
    with open(raw, "ab") as f:
        f.write(audio)


def test_core_stops_only_in_handsfree_and_after_speech(tmp_path, monkeypatch):
    core, raw = make_core(tmp_path, monkeypatch)
    append(raw, pcm(8000, 0.8) + pcm(0, 1.0))
    assert core.autostop_due() is False          # Halten-Modus: nie
    core.enable_streaming()                       # Doppeltipp -> Freihand
    assert core._autostop is not None
    assert core.autostop_due() is True
    assert any("auto-stop" in line for line in core.logs)


def test_core_without_the_switch_never_stops(tmp_path, monkeypatch):
    core, raw = make_core(tmp_path, monkeypatch, enabled=False)
    append(raw, pcm(8000, 0.8) + pcm(0, 5.0))
    core.enable_streaming()
    assert core._autostop is None
    assert core.autostop_due() is False


def test_core_waits_for_speech_before_stopping(tmp_path, monkeypatch):
    core, raw = make_core(tmp_path, monkeypatch)
    core.enable_streaming()
    append(raw, pcm(0, 3.0))                      # nur Stille
    assert core.autostop_due() is False


def test_core_does_not_stop_when_no_recording_runs(tmp_path, monkeypatch):
    core, raw = make_core(tmp_path, monkeypatch)
    core.enable_streaming()
    append(raw, pcm(8000, 0.8) + pcm(0, 1.0))
    core.rec.active = False
    assert core.autostop_due() is False


def test_arming_failure_does_not_break_the_handsfree_switch(tmp_path, monkeypatch):
    core, raw = make_core(tmp_path, monkeypatch)
    monkeypatch.setattr(vad, "make_autostop",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    core.enable_streaming()
    assert core._handsfree is True and core._autostop is None
    assert any("not armed" in line for line in core.logs)


# ---------------------------------------------------------- Ereignis-Thread (Pump)
A, B = 10, 20


class DueCore:
    def __init__(self):
        self.order, self.due = [], False
        self.rec = SimpleNamespace(active=False)

    def start_recording(self):
        self.rec.active = True
        self.order.append("start")
        return True

    def finish(self):
        self.rec.active = False
        self.order.append("finish")
        return None

    def cancel_recording(self, reason):
        self.order.append("cancel")

    def enable_streaming(self):
        self.order.append("handsfree")

    def autostop_due(self):
        return self.due and self.rec.active


class Runner:
    current_action = None

    def submit(self, name, fn):
        pass


def pump_in_handsfree():
    core = DueCore()
    events = queue.Queue()
    pump = dictation.ChordPump(events, core, Runner(), {A}, {B},
                               hold_min=0.3, double_window=0.3)

    def chord(down, t):
        for key in (A, B) if down else (B, A):
            events.put((key, down))
            pump.step(timeout=0, now=t)

    chord(True, 100.0)
    chord(False, 100.1)                           # kurzer Tipp
    chord(True, 100.2)                            # zweiter Tipp: Freihand
    chord(False, 100.3)
    assert pump.machine.state == "toggle" and core.order == ["start", "handsfree"]
    return pump, core


def test_pump_finishes_like_a_second_press_when_silence_is_due():
    pump, core = pump_in_handsfree()
    pump.step(timeout=0, now=105.0)
    assert core.order == ["start", "handsfree"]   # noch nicht faellig
    core.due = True
    pump.step(timeout=0, now=106.0)
    assert core.order == ["start", "handsfree", "finish"]
    assert pump.machine.state == "idle" and not pump.machine.pending_finish


def test_pump_waits_for_the_released_keys():
    pump, core = pump_in_handsfree()
    pump.machine.pressed.add(A)                   # Taste noch gehalten
    core.due = True
    pump.step(timeout=0, now=106.0)
    assert core.order == ["start", "handsfree"]
    pump.machine.pressed.clear()
    pump.step(timeout=0, now=106.1)
    assert core.order[-1] == "finish"


def test_pump_ignores_autostop_in_hold_mode():
    core = DueCore()
    events = queue.Queue()
    pump = dictation.ChordPump(events, core, Runner(), {A}, {B})
    for key in (A, B):
        events.put((key, True))
        pump.step(timeout=0, now=100.0)
    core.due = True
    pump.step(timeout=0, now=101.0)
    assert core.order == ["start"] and pump.machine.state == "hold"
