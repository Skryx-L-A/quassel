"""Tests der gemeinsamen Ablaufsteuerung (quassel/dictation.py): Aktions-
Thread, Ereignis-Thread und ihre Garantien — ohne Mikrofon, Server oder
Tastatur."""
import os
import queue
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import dictation


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


# ------------------------------------------------------------ ActionRunner
def test_runner_executes_in_order_and_survives_an_exception():
    runner = dictation.ActionRunner(log=lambda m: None)
    try:
        done = []
        runner.submit("a", lambda: done.append("a"))
        runner.submit("boom", lambda: 1 / 0)
        runner.submit("b", lambda: done.append("b"))
        assert wait_for(lambda: done == ["a", "b"]), done
        assert wait_for(lambda: runner.current_action is None)
    finally:
        runner.stop()


def test_abandon_leaves_a_hanging_action_and_runs_the_next():
    """Not-Aus: ein hängender Rest blockiert nicht jedes weitere Diktat."""
    runner = dictation.ActionRunner(log=lambda m: None)
    release = threading.Event()
    try:
        done = []
        runner.submit("hang", lambda: release.wait(5) and done.append("late"))
        assert wait_for(lambda: runner.current_action == "hang")
        runner.submit("next", lambda: done.append("next"))
        assert runner.abandon() is True
        assert wait_for(lambda: done == ["next"]), done
        assert wait_for(lambda: runner.current_action is None)
        assert runner.abandon() is False             # nichts läuft mehr
    finally:
        release.set()
        runner.stop()


def test_run_blocking_returns_when_the_action_is_abandoned():
    runner = dictation.ActionRunner(log=lambda m: None)
    release = threading.Event()
    try:
        threading.Timer(0.2, runner.abandon).start()
        t0 = time.monotonic()
        assert runner.run_blocking("hang", lambda: release.wait(5), poll=0.02) is False
        assert time.monotonic() - t0 < 2.0
    finally:
        release.set()
        runner.stop()


def test_run_blocking_reports_a_stall_once():
    runner = dictation.ActionRunner(log=lambda m: None)
    stalls = []
    try:
        assert runner.run_blocking("slow", lambda: time.sleep(0.25),
                                   on_stall=stalls.append, stall_after=0.05,
                                   poll=0.02) is True
        assert len(stalls) == 1
    finally:
        runner.stop()


# --------------------------------------------------------------- ChordPump
A, B = 10, 20


class OrderCore:
    """Ablaufsteuerung-Ersatz: zeichnet die Reihenfolge auf."""

    def __init__(self):
        self.order = []
        self.rec = SimpleNamespace(active=False)
        self.n = 0

    def start_recording(self):
        self.n += 1
        self.order.append(("start", self.n))
        self.rec.active = True
        return True

    def finish(self):
        n = self.n
        self.rec.active = False
        self.order.append(("stopped", n))
        return lambda: self.order.append(("rest", n))

    def cancel_recording(self, reason):
        self.order.append(("cancel", reason))
        self.rec.active = False

    def enable_streaming(self):
        self.order.append(("handsfree",))


class ListRunner:
    def __init__(self):
        self.items = []
        self.current_action = None

    def submit(self, name, fn):
        self.items.append((name, fn))


def make_pump(core=None):
    core = core or OrderCore()
    events, runner = queue.Queue(), ListRunner()
    pump = dictation.ChordPump(events, core, runner, {A}, {B},
                               hold_min=0.3, double_window=0.3)
    return pump, events, core, runner


def chord(pump, events, down, t):
    for key in (A, B) if down else (B, A):
        events.put((key, down))
        pump.step(timeout=0, now=t)


def test_pump_stops_synchronously_and_hands_the_rest_to_the_runner():
    pump, events, core, runner = make_pump()
    chord(pump, events, True, 100.0)
    chord(pump, events, False, 101.0)
    chord(pump, events, True, 101.05)              # nächstes Diktat sofort
    assert core.order == [("start", 1), ("stopped", 1), ("start", 2)]
    assert [name for name, _ in runner.items] == ["finish"]
    runner.items[0][1]()
    assert core.order[-1] == ("rest", 1)


def test_failed_start_does_not_arm_the_machine():
    core = OrderCore()
    core.start_recording = lambda: False
    pump, events, core, runner = make_pump(core)
    chord(pump, events, True, 100.0)
    chord(pump, events, False, 101.0)
    assert pump.machine.state == "idle" and runner.items == []


def test_keys_are_ignored_while_disabled():
    pump, events, core, runner = make_pump()
    pump.is_enabled = lambda: False
    chord(pump, events, True, 100.0)
    assert core.order == []


def test_posted_calls_run_on_the_pump_before_the_next_key():
    pump, events, core, runner = make_pump()
    pump.post(lambda: core.order.append(("posted",)))
    chord(pump, events, True, 100.0)
    assert core.order[0] == ("posted",)


def test_reconfigure_waits_for_idle():
    pump, events, core, runner = make_pump()
    chord(pump, events, True, 100.0)               # Aufnahme läuft
    pump.reconfigure({30}, {40}, 0.4, 0.5)
    pump.step(timeout=0, now=100.1)
    assert pump.machine.a == {A}                   # mitten im Diktat: nicht
    assert pump.machine.hold_min == 0.4            # Zeiten greifen sofort
    chord(pump, events, False, 101.0)
    pump.reconfigure({30}, {40}, 0.4, 0.5)
    pump.step(timeout=0, now=101.1)
    assert pump.machine.a == {30} and pump.machine.b == {40}


def test_force_reset_returns_to_idle():
    pump, events, core, runner = make_pump()
    chord(pump, events, True, 100.0)
    pump.force_reset()
    assert pump.machine.state == "idle" and not pump.machine.pressed


def test_a_failing_action_does_not_kill_the_pump():
    core = OrderCore()

    def boom():
        raise RuntimeError("Gerät weg")
    core.finish = boom
    pump, events, core, runner = make_pump(core)
    chord(pump, events, True, 100.0)
    chord(pump, events, False, 101.0)
    chord(pump, events, True, 102.0)
    assert core.order[-1] == ("start", 2)
