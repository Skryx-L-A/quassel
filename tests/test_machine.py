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

if __name__ == "__main__":
    for fn in [test_hold_to_talk, test_single_tap_cancels, test_double_tap_handsfree, test_other_key_cancels]:
        fn(); print("ok:", fn.__name__)
    print("ALL MACHINE TESTS PASSED")
