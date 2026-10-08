"""Linux-Daemon: der lange Teil eines Diktats hängt den Loop nicht mehr auf,
und der Not-Aus greift immer (Audit D55). Ohne /dev/input und ohne Server."""
import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import daemon as daemon_mod
from quassel import dictation


def make(monkeypatch, rest):
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.rec = SimpleNamespace(active=False, stop=lambda: None, stream_abandoned=False)
    d.partial = None
    d.streamer = None
    d.cfg = SimpleNamespace(beep=False)
    d.ducker = SimpleNamespace(restore=lambda: None)
    seen = {"logs": [], "states": [], "dumps": 0}
    monkeypatch.setattr(daemon_mod, "log", seen["logs"].append)
    monkeypatch.setattr(daemon_mod, "notify", lambda text, ms=4000: None)
    monkeypatch.setattr(daemon_mod, "state_set",
                        lambda s, text="": seen["states"].append(s))
    monkeypatch.setattr(daemon_mod.faulthandler, "dump_traceback",
                        lambda *a, **k: seen.__setitem__("dumps", seen["dumps"] + 1))
    d.finish = lambda: rest
    d._runner = dictation.ActionRunner(log=lambda m: None)
    return d, seen


def test_panic_frees_the_loop_from_a_hanging_rest(monkeypatch):
    """Ein hängendes wl-paste o.ä.: der Loop wartet, bis der Not-Aus kommt —
    dann kehrt er zurück, statt für immer stehen zu bleiben."""
    hang = threading.Event()
    d, seen = make(monkeypatch, lambda: hang.wait(10))
    try:
        threading.Timer(0.3, d.panic_stop).start()   # wie der SIGUSR2-Handler
        t0 = time.monotonic()
        assert d._linux_finish() is False
        assert time.monotonic() - t0 < 3.0
        assert seen["states"] == ["idle"]
        assert d._runner.current_action is None       # nächstes Diktat frei
    finally:
        hang.set()
        d._runner.stop()


def test_a_hanging_rest_is_reported_once(monkeypatch):
    monkeypatch.setattr(daemon_mod, "ACTION_STALL", 0.05)
    d, seen = make(monkeypatch, lambda: time.sleep(0.3))
    try:
        assert d._linux_finish() is True
        assert seen["dumps"] == 1
        assert sum("hängt" in m for m in seen["logs"]) == 1
    finally:
        d._runner.stop()


def test_normal_finish_runs_the_rest(monkeypatch):
    ran = []
    d, seen = make(monkeypatch, lambda: ran.append(1))
    try:
        assert d._linux_finish() is True
        assert ran == [1] and seen["dumps"] == 0
    finally:
        d._runner.stop()


def test_too_short_dictation_has_no_rest(monkeypatch):
    d, seen = make(monkeypatch, None)
    try:
        assert d._linux_finish() is True
    finally:
        d._runner.stop()


def test_the_panic_signal_is_installed_on_linux_too(monkeypatch):
    """Der Linux-Loop registriert SIGUSR2 (vorher nur macOS)."""
    import inspect
    src = inspect.getsource(daemon_mod.Daemon._run_linux)
    assert "_install_panic_signal()" in src
    assert "ActionRunner" in src
