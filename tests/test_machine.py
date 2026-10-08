"""Tests der Chord-Zustandsmaschine (geteilte Halten/Doppeltipp-Logik)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.win.machine import ChordMachine

A, B = {29}, {125}  # je eine Taste pro Gruppe

def make():
    ev = {"start": 0, "finish": 0, "cancel": []}
    m = ChordMachine(A, B,
                     on_start=lambda: ev.__setitem__("start", ev["start"]+1),
                     on_finish=lambda: ev.__setitem__("finish", ev["finish"]+1),
                     on_cancel=lambda r: ev["cancel"].append(r),
                     hold_min=0.5, double_window=0.45)
    return m, ev

def test_hold_to_talk():
    m, ev = make()
    t = 100.0
    m.key(29, True, t); m.key(125, True, t)         # Chord runter
    m.key(125, False, t+1.0); m.key(29, False, t+1.0)  # nach >0,5 s los
    m.poll(t+1.0)
    assert ev["start"] == 1 and ev["finish"] == 1 and not ev["cancel"], ev

def test_single_tap_cancels():
    m, ev = make()
    t = 200.0
    m.key(29, True, t); m.key(125, True, t)
    m.key(125, False, t+0.1); m.key(29, False, t+0.1)  # kurzer Tipp
    m.poll(t+0.7)                                       # Doppeltipp-Fenster vorbei
    assert ev["start"] == 1 and ev["finish"] == 0 and ev["cancel"] == ["canceled_tap"], ev

def test_double_tap_handsfree():
    m, ev = make()
    t = 300.0
    # 1. Tipp
    m.key(29, True, t); m.key(125, True, t)
    m.key(125, False, t+0.1); m.key(29, False, t+0.1)
    # 2. Tipp innerhalb des Fensters -> Freihand (toggle)
    m.key(29, True, t+0.3); m.key(125, True, t+0.3)
    m.key(125, False, t+0.4); m.key(29, False, t+0.4)
    m.poll(t+0.5)
    assert ev["finish"] == 0 and not ev["cancel"], ("freihand läuft", ev)
    # Abschluss-Druck -> einfügen
    m.key(29, True, t+5.0); m.key(125, True, t+5.0)
    m.key(125, False, t+5.1); m.key(29, False, t+5.1)
    m.poll(t+5.1)
    assert ev["finish"] == 1, ev

def test_other_key_cancels():
    m, ev = make()
    t = 400.0
    m.key(29, True, t); m.key(125, True, t)
    m.key(65, True, t+0.2)                  # andere Taste während Chord
    assert ev["cancel"] == ["canceled_key"], ev

# ---------------------------------------------- Linux-Schleife (Audit B35)
# Die evdev-Schleife in daemon.py hat eine eigene Zustandsführung; geprüft
# werden hier ihre Bausteine, ohne /dev/input.
from types import SimpleNamespace


def test_pressed_keys_are_kept_per_device():
    from quassel.daemon import PressedKeys
    keys = PressedKeys()
    keys.press(3, 29)                      # Strg auf Tastatur 3
    keys.press(4, 125)                     # Meta auf Tastatur 4
    assert keys.chord(A, B)
    assert keys.drop(4) is True            # Tastatur 4 schläft ein
    assert not keys.chord(A, B)
    assert keys.codes() == {29}
    assert keys.drop(4) is False
    keys.release(3, 29)
    assert not keys


def test_lost_keyboard_ends_a_held_dictation():
    from quassel.daemon import keys_lost
    assert keys_lost("hold", chord_down=False) == ("idle", True)
    assert keys_lost("hold", chord_down=True) == ("hold", False)
    assert keys_lost("toggle_armed", chord_down=False) == ("toggle", False)
    assert keys_lost("toggle", chord_down=False) == ("toggle", False)
    assert keys_lost("drain", chord_down=False) == ("idle", False)
    assert keys_lost("idle", chord_down=False) == ("idle", False)


def _daemon(active=True, started=0.0):
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.rec = SimpleNamespace(active=active, started=started)
    d.cfg = SimpleNamespace(chord="ctrl+meta")
    return d, daemon_mod


def test_max_record_also_ends_the_hold_mode():
    d, daemon_mod = _daemon(started=100.0)
    late = 100.0 + daemon_mod.MAX_RECORD + 1
    for st in ("hold", "toggle", "toggle_armed"):
        assert d._record_expired(st, late), st
    assert not d._record_expired("hold", 100.0 + daemon_mod.MAX_RECORD - 1)
    assert not d._record_expired("await2", late)
    d.rec.active = False
    assert not d._record_expired("hold", late)


def test_forgetting_a_lost_device_releases_the_chord(monkeypatch):
    d, daemon_mod = _daemon()
    monkeypatch.setattr(daemon_mod, "log", lambda m: None)
    group_a, group_b = daemon_mod.CHORDS["ctrl+meta"]
    keys = daemon_mod.PressedKeys()
    keys.press(7, next(iter(group_a)))
    keys.press(7, next(iter(group_b)))
    assert d._forget_lost_keys(keys, [9], "hold") == ("hold", False)   # anderes Gerät
    assert d._forget_lost_keys(keys, [7], "hold") == ("idle", True)
    assert not keys


def test_scan_devices_reports_closed_devices_before_reuse(monkeypatch):
    from quassel import daemon as daemon_mod
    listing = ["event0", "event1"]
    closed = []
    opened = iter([10, 11, 12])
    monkeypatch.setattr(daemon_mod.os, "listdir", lambda p: list(listing))
    monkeypatch.setattr(daemon_mod.os, "open", lambda p, flags: next(opened))
    monkeypatch.setattr(daemon_mod.os, "close", closed.append)
    fds, gone = {}, []
    daemon_mod.scan_devices(fds, on_close=gone.append)
    assert set(fds) == {10, 11}
    listing.remove("event1")
    daemon_mod.scan_devices(fds, on_close=gone.append)
    assert gone == [11] and closed == [11] and set(fds) == {10}


if __name__ == "__main__":
    for fn in [test_hold_to_talk, test_single_tap_cancels, test_double_tap_handsfree, test_other_key_cancels]:
        fn(); print("ok:", fn.__name__)
    print("ALL MACHINE TESTS PASSED")
