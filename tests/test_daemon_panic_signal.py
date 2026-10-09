"""Review 2026-10-09, Befund 7: der SIGUSR2-Not-Aus läuft nicht im
Signal-Handler selbst. Python führt den Handler auf dem Hauptthread aus,
zwischen zwei Bytecodes — hält der gerade die Zustands- oder Recorder-Sperre,
wartete panic_stop() auf seinen eigenen Thread, und der Daemon stünde."""
import threading
import time

from quassel import daemon as daemon_mod


def install(monkeypatch, d):
    handlers = {}
    monkeypatch.setattr(daemon_mod.signal, "signal",
                        lambda sig, fn: handlers.__setitem__(sig, fn))
    d._install_panic_signal()
    return handlers[daemon_mod.signal.SIGUSR2]


def test_panic_signal_does_not_wait_for_a_lock_its_own_thread_holds(monkeypatch):
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    lock = threading.Lock()                 # wie state_set / Recorder._lock
    got = []

    def panic_stop(*_):
        got.append(lock.acquire(timeout=3))
        if got[-1]:
            lock.release()
    d.panic_stop = panic_stop
    handler = install(monkeypatch, d)
    with lock:                              # der Hauptthread steckt in state_set
        t0 = time.monotonic()
        handler(daemon_mod.signal.SIGUSR2, None)
        assert time.monotonic() - t0 < 2.0
    deadline = time.monotonic() + 3
    while not got and time.monotonic() < deadline:
        time.sleep(0.01)
    assert got == [True]


def test_panic_signal_still_runs_promptly_when_nothing_is_held(monkeypatch):
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    calls = []
    d.panic_stop = lambda *_: calls.append(threading.current_thread().name)
    install(monkeypatch, d)(daemon_mod.signal.SIGUSR2, None)
    assert calls == ["panic"]
