"""Tests der mac-Event-Zuordnung (hotkey_mac.py) ohne echtes Quartz/pyobjc.

FakeKeyboard erzeugt (Keycode, Flag-Masken)-Paare exakt so, wie macOS sie
liefert: SEITEN-Keycode, aber nur ein FAMILIEN-Bit — d.h. das Loslassen
einer Seite, während die andere hält, kommt mit GESETZTEM Bit an. Damit
sind die Tests echte Verhaltenstests gegen die reale Event-Semantik,
keine Mock-Echos."""
import os
import pathlib
import queue
import re
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel.win.machine import ChordMachine
from quassel import hotkey_mac
from quassel.hotkey_mac import (VK_CONTROL, VK_COMMAND, VK_OPTION, MAC_CHORDS,
                                _FAMILIES, MacHotkeyListener,
                                check_permissions, decode_flags_changed,
                                handle_flags_changed, handle_key_down)

L_CTRL, R_CTRL = 0x3B, 0x3E
L_CMD, R_CMD = 0x37, 0x36
L_OPT = 0x3A
L_SHIFT = 0x38
FN = 0x3F
CAPS = 0x39


class FakeKeyboard:
    """Simuliert die physische Tastatur: liefert pro Tastendruck/-loslassen
    das (Keycode, Masken)-Paar, das ein CGEventFlagsChanged tragen würde
    (Familien-Bit = irgendeine Seite der Familie gedrückt)."""

    def __init__(self):
        self.held = set()

    def event(self, keycode, pressed):
        if pressed:
            self.held.add(keycode)
        else:
            self.held.discard(keycode)
        masks = {fam: bool(self.held & keys) for fam, keys in _FAMILIES.items()}
        return keycode, masks


def make():
    ev = {"start": 0, "finish": 0, "cancel": []}
    m = ChordMachine(VK_CONTROL, VK_COMMAND,
                     on_start=lambda: ev.__setitem__("start", ev["start"] + 1),
                     on_finish=lambda: ev.__setitem__("finish", ev["finish"] + 1),
                     on_cancel=lambda r: ev["cancel"].append(r),
                     hold_min=0.5, double_window=0.45)
    return m, ev, FakeKeyboard(), set()


def feed(m, down, kb, keycode, pressed, t):
    kc, masks = kb.event(keycode, pressed)
    handle_flags_changed(m, down, kc, masks, t)


# ------------------------------------------------------------ Grundverhalten

def test_mac_chords_default_is_ctrl_cmd():
    assert MAC_CHORDS["ctrl+meta"] == (VK_CONTROL, VK_COMMAND)


def test_hold_to_talk():
    m, ev, kb, down = make()
    t = 100.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    feed(m, down, kb, L_CMD, False, t + 1.0); feed(m, down, kb, L_CTRL, False, t + 1.0)
    m.poll(t + 1.0)
    assert ev["start"] == 1 and ev["finish"] == 1 and not ev["cancel"], ev


def test_too_short_tap_cancels():
    m, ev, kb, down = make()
    t = 200.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    feed(m, down, kb, L_CMD, False, t + 0.1); feed(m, down, kb, L_CTRL, False, t + 0.1)
    m.poll(t + 0.7)   # Doppeltipp-Fenster (0.45s) vorbei
    assert ev["start"] == 1 and ev["finish"] == 0
    assert ev["cancel"] == ["canceled_tap"], ev


def test_double_tap_handsfree():
    m, ev, kb, down = make()
    handsfree = {"n": 0}
    m.on_handsfree = lambda: handsfree.__setitem__("n", handsfree["n"] + 1)
    t = 300.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    feed(m, down, kb, L_CMD, False, t + 0.1); feed(m, down, kb, L_CTRL, False, t + 0.1)
    feed(m, down, kb, L_CTRL, True, t + 0.3); feed(m, down, kb, L_CMD, True, t + 0.3)
    feed(m, down, kb, L_CMD, False, t + 0.4); feed(m, down, kb, L_CTRL, False, t + 0.4)
    assert handsfree["n"] == 1
    assert ev["finish"] == 0 and not ev["cancel"]


def test_other_key_down_cancels():
    m, ev, kb, down = make()
    t = 400.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    handle_key_down(m, 0x00, t + 0.2)   # kVK_ANSI_A: keine Modifier-Taste
    assert ev["cancel"] == ["canceled_key"], ev


def test_handle_key_down_ignores_modifier_codes():
    m, ev, kb, down = make()
    t = 500.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    for kc in (L_CTRL, L_SHIFT, FN, CAPS):
        handle_key_down(m, kc, t + 0.1)  # Modifier zählen nicht als "andere Taste"
    assert ev["cancel"] == []


# ------------------------------------------- C1: Seiten-Dekodierung der Flags

def test_c1_second_side_tap_does_not_wedge_machine():
    """Reproduziertes Review-Szenario: L-Strg+Cmd halten, R-Strg antippen,
    alles loslassen -> finish MUSS feuern (vorher: pressed={0x3E} für immer,
    pending_finish hing, Diktat wurde nie eingefügt)."""
    m, ev, kb, down = make()
    t = 600.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    # R-Strg tippen: Loslassen kommt mit GESETZTEM Control-Bit (L-Strg hält)
    feed(m, down, kb, R_CTRL, True, t + 0.2)
    feed(m, down, kb, R_CTRL, False, t + 0.3)
    feed(m, down, kb, L_CMD, False, t + 1.0)
    feed(m, down, kb, L_CTRL, False, t + 1.0)
    m.poll(t + 1.0)
    assert ev["start"] == 1 and ev["finish"] == 1, ev
    assert not m.pressed and not m.pending_finish and m.state == "idle"


def test_c1_release_one_side_keeps_chord_of_other():
    """Beide Strg-Seiten + Cmd halten, eine Strg-Seite loslassen: Chord
    besteht weiter (andere Seite hält), kein finish."""
    m, ev, kb, down = make()
    t = 700.0
    feed(m, down, kb, L_CTRL, True, t)
    feed(m, down, kb, R_CTRL, True, t)
    feed(m, down, kb, L_CMD, True, t)
    feed(m, down, kb, L_CTRL, False, t + 0.6)   # Bit bleibt gesetzt (R-Strg hält)
    m.poll(t + 0.7)
    assert ev["start"] == 1 and ev["finish"] == 0
    feed(m, down, kb, L_CMD, False, t + 1.0)
    feed(m, down, kb, R_CTRL, False, t + 1.0)
    m.poll(t + 1.0)
    assert ev["finish"] == 1, ev


def test_decode_bit_cleared_releases_all_sides():
    """Gelöschtes Familien-Bit lässt ALLE getrackten Seiten der Familie los
    (auch wenn ein Zwischen-Event verloren ging)."""
    down = set()
    assert decode_flags_changed(down, L_CTRL, {"control": True}) == [(L_CTRL, True)]
    assert decode_flags_changed(down, R_CTRL, {"control": True}) == [(R_CTRL, True)]
    rel = decode_flags_changed(down, L_CTRL, {"control": False})
    assert sorted(rel) == sorted([(L_CTRL, False), (R_CTRL, False)])
    assert not down


def test_decode_toggle_press_then_release_same_side():
    down = set()
    assert decode_flags_changed(down, R_CTRL, {"control": True}) == [(R_CTRL, True)]
    # Bit noch gesetzt (z.B. andere Seite hält) -> dieselbe Seite = Loslassen
    assert decode_flags_changed(down, R_CTRL, {"control": True}) == [(R_CTRL, False)]
    assert not down


# --------------------------------------------- M6: Shift/Fn/CapsLock brechen ab

def test_m6_shift_during_hold_cancels():
    m, ev, kb, down = make()
    t = 800.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    feed(m, down, kb, L_SHIFT, True, t + 0.2)
    assert ev["cancel"] == ["canceled_key"], ev


def test_m6_fn_and_capslock_during_hold_cancel():
    for kc in (FN, CAPS):
        m, ev, kb, down = make()
        t = 900.0
        feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
        feed(m, down, kb, kc, True, t + 0.2)
        assert ev["cancel"] == ["canceled_key"], (hex(kc), ev)


def test_m6_shift_release_does_not_cancel():
    """Shift war schon vor dem Chord gedrückt; sein Loslassen währenddessen
    ist kein Abbruch (Parität zu Linux: nur Tastendrücke brechen ab)."""
    m, ev, kb, down = make()
    t = 1000.0
    feed(m, down, kb, L_SHIFT, True, t - 1.0)          # vor dem Chord
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    feed(m, down, kb, L_SHIFT, False, t + 0.2)
    assert ev["cancel"] == [] and ev["start"] == 1


def test_m6_shift_while_idle_is_ignored():
    m, ev, kb, down = make()
    feed(m, down, kb, L_SHIFT, True, 1100.0)
    feed(m, down, kb, L_SHIFT, False, 1100.1)
    assert ev == {"start": 0, "finish": 0, "cancel": []}


# ------------------------------------- H4: fehlgeschlagener Start armiert nicht

def test_h4_failed_start_does_not_arm():
    ev = {"start": 0, "finish": 0}
    m = ChordMachine(VK_CONTROL, VK_COMMAND,
                     on_start=lambda: (ev.__setitem__("start", ev["start"] + 1), False)[1],
                     on_finish=lambda: ev.__setitem__("finish", ev["finish"] + 1),
                     on_cancel=lambda r: None, hold_min=0.5, double_window=0.45)
    kb, down = FakeKeyboard(), set()
    t = 1200.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    assert ev["start"] == 1
    assert m.state == "idle"                       # nicht armiert
    feed(m, down, kb, L_CMD, False, t + 1.0); feed(m, down, kb, L_CTRL, False, t + 1.0)
    m.poll(t + 1.0)
    assert ev["finish"] == 0                       # kein Replay des alten Diktats
    assert not m.pending_finish


def test_h4_start_can_succeed_after_failure():
    results = [False, True]
    ev = {"start": 0, "finish": 0}
    m = ChordMachine(VK_CONTROL, VK_COMMAND,
                     on_start=lambda: (ev.__setitem__("start", ev["start"] + 1),
                                       results[ev["start"] - 1])[1],
                     on_finish=lambda: ev.__setitem__("finish", ev["finish"] + 1),
                     on_cancel=lambda r: None, hold_min=0.5, double_window=0.45)
    kb, down = FakeKeyboard(), set()
    t = 1300.0
    feed(m, down, kb, L_CTRL, True, t); feed(m, down, kb, L_CMD, True, t)
    feed(m, down, kb, L_CMD, False, t + 1.0); feed(m, down, kb, L_CTRL, False, t + 1.0)
    m.poll(t + 1.0)
    feed(m, down, kb, L_CTRL, True, t + 2.0); feed(m, down, kb, L_CMD, True, t + 2.0)
    assert m.state == "hold"
    feed(m, down, kb, L_CMD, False, t + 3.0); feed(m, down, kb, L_CTRL, False, t + 3.0)
    m.poll(t + 3.0)
    assert ev["start"] == 2 and ev["finish"] == 1


def test_h4_none_return_still_arms():
    """Rückwärtskompatibel: Callbacks ohne Rückgabewert (Windows) = Erfolg."""
    m, ev, kb, down = make()   # on_start gibt None zurück
    feed(m, down, kb, L_CTRL, True, 1400.0); feed(m, down, kb, L_CMD, True, 1400.0)
    assert m.state == "hold"


# ------------------------------------------------------- Listener (H2/H3/M2/M3)

def make_listener(chord="ctrl+meta", hold_min=0.5, double_window=0.45,
                  on_start=None, on_finish=None, on_cancel=None, on_handsfree=None):
    cfg = SimpleNamespace(hold_min=hold_min, double_window=double_window)
    ev = {"start": 0, "finish": 0, "cancel": [], "handsfree": 0}

    def _start():
        ev["start"] += 1
        return True
    listener = MacHotkeyListener(
        chord, cfg,
        on_start or _start,
        on_finish or (lambda: ev.__setitem__("finish", ev["finish"] + 1)),
        on_cancel or (lambda r: ev["cancel"].append(r)),
        on_handsfree or (lambda: ev.__setitem__("handsfree", ev["handsfree"] + 1)))
    return listener, ev


def flags_item(kb, keycode, pressed, t):
    kc, masks = kb.event(keycode, pressed)
    return ("flags", kc, masks, t)


def drain(listener):
    """Eingereihte Aktionen ausführen — im Betrieb macht das der Aktions-Thread
    (mac-hotkey-actions), im Test synchron und in derselben Reihenfolge."""
    while True:
        try:
            _name, cb = listener._actions.get_nowait()
        except queue.Empty:
            return
        cb()


def process(listener, item, now=None):
    """Ein Event durch den Ereignis-Thread schicken und die dabei fälligen
    Aktionen ausführen (beide Threads zusammen, synchron)."""
    listener._process(item, now=now)
    drain(listener)


def test_listener_hold_to_talk_via_process():
    listener, ev = make_listener()
    kb = FakeKeyboard()
    t = 100.0
    process(listener, flags_item(kb, L_CTRL, True, t), now=t)
    process(listener, flags_item(kb, L_CMD, True, t), now=t)
    assert ev["start"] == 1
    process(listener, flags_item(kb, L_CMD, False, t + 1.0), now=t + 1.0)
    process(listener, flags_item(kb, L_CTRL, False, t + 1.0), now=t + 1.0)
    assert ev["finish"] == 1 and not ev["cancel"], ev


def test_listener_c1_scenario_via_process():
    """C1-Szenario über den kompletten Listener-Pfad (Queue-Items + _process)."""
    listener, ev = make_listener()
    kb = FakeKeyboard()
    t = 200.0
    process(listener, flags_item(kb, L_CTRL, True, t), now=t)
    process(listener, flags_item(kb, L_CMD, True, t), now=t)
    process(listener, flags_item(kb, R_CTRL, True, t + 0.2), now=t + 0.2)
    process(listener, flags_item(kb, R_CTRL, False, t + 0.3), now=t + 0.3)
    process(listener, flags_item(kb, L_CMD, False, t + 1.0), now=t + 1.0)
    process(listener, flags_item(kb, L_CTRL, False, t + 1.0), now=t + 1.0)
    assert ev["start"] == 1 and ev["finish"] == 1, ev
    assert listener.machine.state == "idle" and not listener.machine.pressed


class _FakeQuartz:
    """Minimales Quartz-Double für _callback- und check_permissions-Tests."""
    kCGEventTapDisabledByTimeout = 0xFFFFFFFE
    kCGEventTapDisabledByUserInput = 0xFFFFFFFF
    kCGEventFlagsChanged = 12
    kCGEventKeyDown = 10
    kCGKeyboardEventKeycode = 9
    kCGEventFlagMaskControl = 1 << 18
    kCGEventFlagMaskCommand = 1 << 20
    kCGEventFlagMaskAlternate = 1 << 19
    kCGEventFlagMaskShift = 1 << 17
    kCGEventFlagMaskSecondaryFn = 1 << 23
    kCGEventFlagMaskAlphaShift = 1 << 16

    def __init__(self, keycode=0, flags=0):
        self.enabled = []
        self._keycode = keycode
        self._flags = flags

    def CGEventTapEnable(self, tap, on):
        self.enabled.append((tap, on))

    def CGEventGetIntegerValueField(self, event, field):
        return self._keycode

    def CGEventGetFlags(self, event):
        return self._flags


def test_h2_callback_reenables_disabled_tap(monkeypatch):
    listener, ev = make_listener()
    fake = _FakeQuartz()
    monkeypatch.setitem(sys.modules, "Quartz", fake)
    tap = object()
    listener._tap = tap
    for etype in (fake.kCGEventTapDisabledByTimeout,
                  fake.kCGEventTapDisabledByUserInput):
        listener._callback(None, etype, object(), None)
    assert fake.enabled == [(tap, True), (tap, True)]
    assert listener._events.empty()          # Disabled-Events landen nicht in der Queue
    assert ev["start"] == 0 and ev["finish"] == 0


def test_h2_callback_only_enqueues(monkeypatch):
    """Der Tap-Callback fasst die Maschine nicht an — er reiht nur ein."""
    listener, ev = make_listener()
    fake = _FakeQuartz(keycode=L_CTRL,
                       flags=_FakeQuartz.kCGEventFlagMaskControl)
    monkeypatch.setitem(sys.modules, "Quartz", fake)

    def boom(*a, **k):
        raise AssertionError("machine.key im Tap-Callback aufgerufen")
    monkeypatch.setattr(listener.machine, "key", boom)
    listener._callback(None, fake.kCGEventFlagsChanged, object(), None)
    item = listener._events.get_nowait()
    assert item[0] == "flags" and item[1] == L_CTRL
    assert item[2]["control"] is True and item[2]["command"] is False


def dictate(listener, kb, hold=0.05):
    """Ein volles Halten-Diktat über die Event-Queue schicken (wie der Tap)."""
    listener._events.put(flags_item(kb, L_CTRL, True, time.monotonic()))
    listener._events.put(flags_item(kb, L_CMD, True, time.monotonic()))
    time.sleep(hold)                          # > hold_min
    listener._events.put(flags_item(kb, L_CMD, False, time.monotonic()))
    listener._events.put(flags_item(kb, L_CTRL, False, time.monotonic()))


def test_h3_blocking_rest_does_not_block_events():
    """Ein hängender REST (Transkription) hält den Ereignis-Thread nicht auf:
    das nächste Diktat startet, während er noch läuft, und der Ereignis-Thread
    tickt weiter. Der synchrone Teil des Beendens liegt dagegen immer vor dem
    nächsten Start — die frühere Fassung dieses Tests schrieb die umgekehrte
    Reihenfolge als erlaubt fest, und genau daran verlor ein wartendes
    finish() sein Diktat."""
    order = []
    rest_began = threading.Event()
    second_start = threading.Event()
    all_done = threading.Event()

    def on_start():
        order.append("start")
        if order.count("start") == 2:
            second_start.set()
        return True

    def on_finish():
        nth = order.count("stop") + 1
        order.append("stop")              # synchron, auf dem Ereignis-Thread

        def rest():
            order.append("rest-begin")
            if nth == 1:
                rest_began.set()
                time.sleep(0.3)           # simulierte lange Transkription
            order.append("rest-end")
            if nth == 2:
                all_done.set()
        return rest

    listener, _ = make_listener(hold_min=0.01, on_start=on_start, on_finish=on_finish)
    listener.start_workers()
    try:
        kb = FakeKeyboard()
        dictate(listener, kb)
        assert rest_began.wait(2), "Rest nie gestartet"
        tick = listener.last_event_tick
        dictate(listener, kb)                 # zweites Diktat, während Rest 1 hängt
        assert second_start.wait(2), "zweiter start nie gefeuert: %s" % order
        assert "rest-end" not in order        # Rest 1 läuft noch
        assert listener.current_action == "finish"
        assert listener.last_event_tick > tick, "Ereignis-Thread steht"
        assert all_done.wait(3), order
    finally:
        listener._stop_poll.set()
    assert order == ["start", "stop", "rest-begin", "start", "stop",
                     "rest-end", "rest-begin", "rest-end"], order


def test_action_worker_uses_action_idle_poll_not_poll_interval(monkeypatch):
    """Der Aktions-Thread wacht bei leerer Queue nur an ACTION_IDLE_POLL auf
    (Watchdog misst gegen 60s/30s, ein Sekundentakt reicht) — POLL_INTERVAL
    (0,05s) bleibt dem zeitkritischen _worker vorbehalten. Eine deutlich
    größere Frist als POLL_INTERVAL beweist, dass wirklich ACTION_IDLE_POLL
    gilt und nicht versehentlich weiter POLL_INTERVAL."""
    assert hotkey_mac.ACTION_IDLE_POLL == 1.0
    assert hotkey_mac.ACTION_IDLE_POLL != hotkey_mac.POLL_INTERVAL
    monkeypatch.setattr(hotkey_mac, "ACTION_IDLE_POLL", 0.3)
    listener, _ = make_listener()
    listener.start_workers()
    try:
        listener.last_action_tick = 0.0
        t0 = time.monotonic()
        while listener.last_action_tick < t0:
            time.sleep(0.01)
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.2, "wachte zu früh auf -- benutzt POLL_INTERVAL statt ACTION_IDLE_POLL?"
        assert elapsed < 1.0
    finally:
        listener._stop_poll.set()


def test_force_finish_returns_false_when_lock_is_held(monkeypatch):
    """Hängt ein Callback unter dem Maschinen-Lock (on_start läuft synchron),
    darf force_finish nicht mitwarten — sonst hängt auch der Watchdog."""
    monkeypatch.setattr(hotkey_mac, "LOCK_WAIT", 0.05)
    listener, _ = make_listener()
    logs = []
    monkeypatch.setattr(hotkey_mac, "_log", logs.append)
    listener._lock.acquire()
    try:
        t0 = time.monotonic()
        assert listener.force_finish() is False
        assert time.monotonic() - t0 < 1.0
    finally:
        listener._lock.release()
    assert any("Hotkey-Lock blockiert" in m for m in logs), logs


def test_reconfigure_does_not_hang_on_a_blocked_lock(monkeypatch):
    """reconfigure läuft in der Haushaltsschleife, die auch den Watchdog fährt.
    Hängt sie hier, bleibt jeder Hänger unbemerkt — also lieber nichts tun und
    beim nächsten Durchlauf in fünf Sekunden wiederkommen."""
    monkeypatch.setattr(hotkey_mac, "LOCK_WAIT", 0.05)
    logs = []
    monkeypatch.setattr(hotkey_mac, "_log", logs.append)
    listener, _ = make_listener("ctrl+meta", hold_min=0.5)
    listener._lock.acquire()
    try:
        t0 = time.monotonic()
        listener.reconfigure("ctrl+alt", 0.2, 0.3)
        assert time.monotonic() - t0 < 1.0
    finally:
        listener._lock.release()
    assert listener.machine.hold_min == 0.5          # unverändert
    assert listener.machine.b == VK_COMMAND
    assert any("greifen später" in m for m in logs), logs
    listener.reconfigure("ctrl+alt", 0.2, 0.3)       # Lock frei -> greift
    assert listener.machine.b == VK_OPTION


def test_event_tick_is_refreshed_before_a_slow_start():
    """start_recording läuft synchron auf dem Ereignis-Thread und braucht mit
    osascript seine Zeit. Ohne frischen Zeitstempel meldete der Watchdog dann
    einen Hänger und kippte einen Thread-Dump in genau das Log, das im
    Ernstfall gelesen werden soll.

    Gemessen wird gegen den Zeitpunkt UNMITTELBAR vor dem Aufruf, der den Chord
    schließt: ein Zeitstempel vom Ende des vorherigen _process-Aufrufs liegt
    davor und zählt nicht."""
    seen = {}

    def on_start():
        seen["tick"] = listener.last_event_tick
        return True
    listener, _ = make_listener(hold_min=0.01, on_start=on_start)
    kb = FakeKeyboard()
    process(listener, flags_item(kb, L_CTRL, True, time.monotonic()))
    time.sleep(0.02)
    t_call = time.monotonic()
    process(listener, flags_item(kb, L_CMD, True, t_call))
    assert seen["tick"] >= t_call, "Zeitstempel wurde erst nach on_start gesetzt"


def test_force_reset_returns_the_machine_to_idle():
    listener, _ = make_listener()
    listener.machine.state = "toggle"
    listener.machine.pressed.add(L_CTRL)
    listener.machine.pending_finish = True
    listener.force_reset()
    assert listener.machine.state == "idle"
    assert not listener.machine.pressed
    assert listener.machine.pending_finish is False


def test_force_reset_also_works_without_the_lock(monkeypatch):
    """Nach dem Not-Aus ist ein Wettlauf beim Zurücksetzen hinnehmbar; ein
    dauerhaft in 'toggle' verklemmter Hotkey ist es nicht."""
    monkeypatch.setattr(hotkey_mac, "LOCK_WAIT", 0.05)
    logs = []
    monkeypatch.setattr(hotkey_mac, "_log", logs.append)
    listener, _ = make_listener()
    listener.machine.state = "toggle"
    listener.machine.pending_finish = True
    listener._lock.acquire()
    try:
        listener.force_reset()
    finally:
        listener._lock.release()
    assert listener.machine.state == "idle"
    assert listener.machine.pending_finish is False
    assert any("ohne Lock" in m for m in logs), logs


def test_action_error_does_not_kill_the_action_thread(monkeypatch):
    """Eine geworfene Ausnahme in finish() darf nicht jedes weitere Diktat
    stilllegen — der Aktions-Thread meldet sie und arbeitet weiter."""
    logs = []
    monkeypatch.setattr(hotkey_mac, "_log", logs.append)
    done = threading.Event()
    calls = []

    def on_finish():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Transkription kaputt")
        done.set()

    listener, _ = make_listener(hold_min=0.01, on_finish=on_finish)
    listener.start_workers()
    try:
        kb = FakeKeyboard()
        dictate(listener, kb)
        dictate(listener, kb)
        assert done.wait(2), calls
    finally:
        listener._stop_poll.set()
    assert listener.current_action is None
    assert any("fehlgeschlagen" in m for m in logs), logs


def test_m2_force_finish_ends_handsfree():
    listener, ev = make_listener()
    kb = FakeKeyboard()
    t = 300.0
    # Doppeltipp -> Freihand (toggle)
    process(listener, flags_item(kb, L_CTRL, True, t), now=t)
    process(listener, flags_item(kb, L_CMD, True, t), now=t)
    process(listener, flags_item(kb, L_CMD, False, t + 0.1), now=t + 0.1)
    process(listener, flags_item(kb, L_CTRL, False, t + 0.1), now=t + 0.1)
    process(listener, flags_item(kb, L_CTRL, True, t + 0.3), now=t + 0.3)
    process(listener, flags_item(kb, L_CMD, True, t + 0.3), now=t + 0.3)
    process(listener, flags_item(kb, L_CMD, False, t + 0.4), now=t + 0.4)
    process(listener, flags_item(kb, L_CTRL, False, t + 0.4), now=t + 0.4)
    assert listener.machine.state == "toggle" and ev["handsfree"] == 1
    assert listener.force_finish() is True
    process(listener, None, now=t + 0.5)      # nächster Poll-Tick feuert finish
    assert ev["finish"] == 1
    assert listener.machine.state == "idle" and not listener.machine.pending_finish


def test_m2_force_finish_noop_outside_handsfree():
    listener, ev = make_listener()
    kb = FakeKeyboard()
    assert listener.force_finish() is False   # idle
    t = 400.0
    process(listener, flags_item(kb, L_CTRL, True, t), now=t)
    process(listener, flags_item(kb, L_CMD, True, t), now=t)
    assert listener.machine.state == "hold"
    assert listener.force_finish() is False   # Halten-Modus: kein Limit (wie Linux)
    assert ev["finish"] == 0


def fake_listener(now, force_finish=lambda: True, action=None, state="toggle"):
    """Listener-Attrappe mit denselben Lebenszeichen wie der echte Listener."""
    return SimpleNamespace(force_finish=force_finish,
                           last_event_tick=now, last_action_tick=now,
                           current_action=action, current_sync=None,
                           machine=SimpleNamespace(state=state))


def test_m2_daemon_watchdog_triggers_after_max_record():
    from quassel.daemon import Daemon, MAX_RECORD
    d = Daemon.__new__(Daemon)                # __init__ (Cfg/Recorder) umgehen
    d.rec = SimpleNamespace(active=True, started=100.0)
    calls = []
    now = 100.0 + MAX_RECORD - 1
    listener = fake_listener(now, lambda: calls.append(1) or True)
    d._mac_watchdog(listener, now=now)
    assert calls == []                        # unter dem Limit
    now = 100.0 + MAX_RECORD + 1
    listener.last_event_tick = listener.last_action_tick = now
    d._mac_watchdog(listener, now=now)
    assert calls == [1]
    d.rec = SimpleNamespace(active=False, started=100.0)
    d._mac_watchdog(listener, now=now)
    assert calls == [1]                       # keine aktive Aufnahme -> nichts


def test_watchdog_escalates_to_panic_stop_when_force_finish_fails():
    """force_finish() == False heißt: der Hotkey antwortet nicht mehr. Dann
    muss der Not-Aus greifen, sonst läuft die Aufnahme ewig weiter."""
    from quassel.daemon import Daemon, MAX_RECORD
    d = Daemon.__new__(Daemon)
    d.rec = SimpleNamespace(active=True, started=100.0)
    panics = []
    d.panic_stop = lambda *_: panics.append(1)
    now = 100.0 + MAX_RECORD + 1
    d._mac_watchdog(fake_listener(now, lambda: False), now=now)
    assert panics == [1]


def test_watchdog_stall_warning_is_debounced(monkeypatch):
    """Ein stehender Ereignis-Thread erzeugt GENAU EINE Warnung samt
    Thread-Stacks — nicht alle 5 s eine neue; erst wenn er wieder tickt,
    ist die nächste Warnung wieder frei."""
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.rec = SimpleNamespace(active=True, started=1000.0)
    logs, dumps = [], []
    monkeypatch.setattr(daemon_mod, "log", logs.append)
    monkeypatch.setattr(daemon_mod.faulthandler, "dump_traceback",
                        lambda *a, **k: dumps.append(1))
    now = 1000.0
    listener = fake_listener(now - daemon_mod.EVENT_STALL - 1)
    d._mac_check_stall(listener, now)
    d._mac_check_stall(listener, now + 1)
    assert len(dumps) == 1
    assert sum("hängt" in m for m in logs) == 1, logs
    listener.last_event_tick = now + 2        # Thread lebt wieder
    d._mac_check_stall(listener, now + 2)
    listener.last_event_tick = now - 100      # und hängt erneut
    d._mac_check_stall(listener, now + 3)
    assert len(dumps) == 2


def _stall_daemon(monkeypatch, recording=False):
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.rec = SimpleNamespace(active=recording, started=0.0)
    seen = {"logs": [], "dumps": [], "notes": [], "exits": []}
    monkeypatch.setattr(daemon_mod, "log", seen["logs"].append)
    monkeypatch.setattr(daemon_mod.faulthandler, "dump_traceback",
                        lambda *a, **k: seen["dumps"].append(1))
    monkeypatch.setattr(daemon_mod, "notify",
                        lambda text, ms=4000: seen["notes"].append(text))
    d._exit = lambda code: seen["exits"].append(code)
    return d, seen


def test_dead_event_thread_restarts_the_daemon(monkeypatch):
    """Klemmt schon das Öffnen des Geräts, läuft keine Aufnahme — die
    MAX_RECORD-Eskalation greift dann nie und der Prozess bliebe für immer
    stumm. Also beendet er sich und die Aufsicht startet ihn neu."""
    from quassel import daemon as daemon_mod
    d, seen = _stall_daemon(monkeypatch)
    now = 5000.0
    listener = fake_listener(now - daemon_mod.DEAD_STALL - 1)
    d._mac_check_stall(listener, now)
    assert seen["exits"] == [daemon_mod.RESTART_EXIT]
    assert seen["notes"] == [daemon_mod.tr("audio_restart")]
    assert len(seen["dumps"]) == 1


def test_dead_event_thread_with_running_recording_does_not_restart(monkeypatch):
    """Läuft noch eine Aufnahme, gehört sie zuerst beendet (force_finish bzw.
    Not-Aus) — hier wird der Prozess nicht weggeschossen."""
    from quassel import daemon as daemon_mod
    d, seen = _stall_daemon(monkeypatch, recording=True)
    now = 5000.0
    d._mac_check_stall(fake_listener(now - daemon_mod.DEAD_STALL - 1), now)
    assert seen["exits"] == []


def test_a_working_sync_part_is_not_a_stall(monkeypatch):
    """Der synchrone Teil darf zäh sein: Gerät stoppen und osascript summieren
    sich rechnerisch auf über zwanzig Sekunden. Solange er nachweislich
    arbeitet, ist der Ereignis-Thread nicht still — sonst schlüge der
    Selbstneustart mitten in ein normales Diktatende und würfe die eben
    gelesenen Rohdaten weg."""
    from quassel import daemon as daemon_mod
    d, seen = _stall_daemon(monkeypatch)
    now = 9000.0
    listener = fake_listener(now - daemon_mod.DEAD_STALL - 5)   # 35 s still
    listener.current_sync = "finish"
    d._mac_check_stall(listener, now)
    assert seen["exits"] == [], "Selbstneustart mitten im Diktat"
    assert seen["dumps"] == [], "Fehlalarm bei arbeitendem Thread"
    listener.current_sync = None          # dieselbe Stille ohne erkennbare Arbeit
    d._mac_check_stall(listener, now)
    assert seen["dumps"] == [1]
    assert seen["exits"] == [daemon_mod.RESTART_EXIT]


def test_a_sync_part_that_never_returns_still_restarts(monkeypatch):
    """Bleibt der synchrone Teil ganz stehen (verklemmtes CoreAudio), hilft nur
    noch der Neustart — die lange Frist gilt, aber sie gilt nicht ewig."""
    from quassel import daemon as daemon_mod
    d, seen = _stall_daemon(monkeypatch)
    now = 9000.0
    listener = fake_listener(now - daemon_mod.ACTION_STALL - 1)
    listener.current_sync = "finish"
    d._mac_check_stall(listener, now)
    assert seen["exits"] == [daemon_mod.RESTART_EXIT]


def test_short_stall_warns_but_does_not_restart(monkeypatch):
    from quassel import daemon as daemon_mod
    d, seen = _stall_daemon(monkeypatch)
    now = 5000.0
    listener = fake_listener(now - daemon_mod.EVENT_STALL - 1)
    d._mac_check_stall(listener, now)
    d._mac_check_stall(listener, now + 1)
    assert seen["exits"] == []
    assert len(seen["dumps"]) == 1           # Entprellung gilt weiterhin


def test_watchdog_warns_about_endless_action(monkeypatch):
    """Auch eine Aktion (finish), die 60 s läuft, wird gemeldet — der
    Ereignis-Thread tickt dabei ganz normal weiter."""
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.rec = SimpleNamespace(active=True, started=1000.0)
    logs, dumps = [], []
    monkeypatch.setattr(daemon_mod, "log", logs.append)
    monkeypatch.setattr(daemon_mod.faulthandler, "dump_traceback",
                        lambda *a, **k: dumps.append(1))
    now = 2000.0
    listener = fake_listener(now, action="finish")
    listener.last_action_tick = now - daemon_mod.ACTION_STALL - 1
    d._mac_check_stall(listener, now)
    assert len(dumps) == 1 and any("'finish'" in m for m in logs), logs
    # dieselbe Aktion, aber erst 10 s alt -> keine Meldung
    d._stall_logged = False
    dumps.clear()
    listener.last_action_tick = now - 10
    d._mac_check_stall(listener, now)
    assert dumps == []


# ------------------------------- Wettlauf zwischen neuem Start und finish()

def _rec_daemon(monkeypatch, rec, **cfg_extra):
    """Daemon-Attrappe für start_recording/cancel_recording/finish."""
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.rec = rec
    d.partial = None
    d.streamer = None
    d._clip_backup = None
    d._bt = False
    d._vad = True
    d.last_paste_len = 0
    d.ducker = SimpleNamespace(apply=lambda mode: None, restore=lambda: None)
    cfg = dict(beep=False, mic="default", mute_mode="off", ui_language="auto",
               streaming=False, ai_enabled=False, programmer_mode=False,
               text_replace=False, history_enabled=False, stats_enabled=False,
               auto_learn=False, reload=lambda: False)
    cfg.update(cfg_extra)
    d.cfg = SimpleNamespace(**cfg)
    seen = {"states": [], "notes": [], "restored": []}
    monkeypatch.setattr(daemon_mod, "state_set",
                        lambda s, text="": seen["states"].append((s, text)))
    monkeypatch.setattr(daemon_mod, "notify",
                        lambda text, ms=4000: seen["notes"].append(text))
    monkeypatch.setattr(daemon_mod, "streaming_restore",
                        lambda old: seen["restored"].append(old))
    monkeypatch.setattr(daemon_mod, "log", lambda m: None)
    monkeypatch.setattr(daemon_mod, "TAIL_PAD_MS", 0)
    monkeypatch.setattr(daemon_mod.config, "read_serverenv", lambda: {})
    monkeypatch.setattr(daemon_mod, "mic_is_bluetooth", lambda mic: False)
    monkeypatch.setattr(daemon_mod, "PartialLoop", _NoPartial)
    # Server gilt als warm; der Kaltstart-Fall hat einen eigenen Test.
    monkeypatch.setattr(daemon_mod.whisperclient, "server_was_up", lambda: True)
    return d, seen


def run_finish(daemon):
    """Ein ganzes Diktat abwickeln — genau der Weg, den auch der Linux-Loop
    nimmt: synchroner Teil, danach der Rest."""
    return daemon.finish_now()


class _NoPartial:
    """PartialLoop-Ersatz: die Live-Vorschau gehört nicht zu diesen Tests und
    hinterließe sonst je Test einen wartenden Thread."""

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class SharedRawFile:
    """Recorder-Attrappe auf EINER Rohdatei, wie im Betrieb: start() öffnet sie
    mit 'wb' und kürzt sie damit auf null."""

    def __init__(self, data=b"\x01" * 200000, stop_takes=0.0):
        self.data = data
        self.stop_takes = stop_takes
        self.started = 0.0
        self.active = True
        self.starts = 0
        self.stream_abandoned = False    # macOS: CoreAudio verklemmt

    def start(self, mic="default"):
        self.starts += 1
        self.data = b""
        return True

    def stop(self):
        time.sleep(self.stop_takes)

    def raw_bytes(self, path=None):
        return self.data


def test_finish_captures_the_audio_before_it_returns(monkeypatch):
    """finish() ist zweigeteilt: der synchrone Teil hat die Rohdaten schon
    gelesen, wenn er zurückkehrt. Alles, was danach noch kommt, fasst die
    Aufnahme nicht mehr an — ein neues Diktat kann ihm nichts mehr wegnehmen."""
    from quassel import daemon as daemon_mod
    rec = SharedRawFile()
    d, seen = _rec_daemon(monkeypatch, rec)
    captured = {}
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=600: True)
    monkeypatch.setattr(daemon_mod, "wav_from_raw",
                        lambda data, path: captured.__setitem__("data", data))
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                        lambda *a, **k: "hallo welt")
    monkeypatch.setattr(daemon_mod.textproc, "postprocess",
                        lambda raw, cfg: ("text", "hallo welt"))
    monkeypatch.setattr(daemon_mod, "paste", lambda t: None)
    rest = d.finish()
    assert callable(rest)                    # der lange Teil steht noch aus
    assert d.start_recording() is True        # nächstes Diktat, kürzt die Datei
    assert rec.raw_bytes() == b""
    rest()                                    # erst jetzt die Transkription
    assert set(captured["data"]) == {1}       # trotzdem die eigenen Bytes
    assert len(captured["data"]) > 100000


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


class OrderRecorder:
    """Recorder-Attrappe, die den Pfad wie das Original erst IN stop() liest —
    nur so ist die gefährliche Reihenfolge überhaupt nachzubauen. `leaked` hält
    fest, welche Aufnahme nie beendet wurde und weiterliefe."""

    def __init__(self):
        self.paths = ["rec.raw", "rec-b.raw"]
        self.slot = -1
        self.raw_path = None
        self.running = None
        self.stopped = []
        self.leaked = []
        self.started = 0.0
        self.stream_abandoned = False

    @property
    def active(self):
        return self.running is not None

    def start(self, mic="default"):
        if self.running is not None:
            self.leaked.append(self.running)
        self.slot = (self.slot + 1) % 2
        self.raw_path = self.running = self.paths[self.slot]
        return True

    def stop(self):
        path = self.raw_path
        if path is not None:
            self.stopped.append(path)
        self.running = None
        return path

    def raw_bytes(self, path=None):
        return b"\x01" * 200000


class OrderDaemon:
    """Daemon-Ersatz mit derselben Zweiteilung wie der echte: finish() stoppt
    synchron und gibt den langen Rest als Callable zurück."""

    def __init__(self, rec, order):
        self.rec = rec
        self.order = order

    def start_recording(self):
        self.order.append("start")
        return self.rec.start()

    def finish(self):
        path = self.rec.stop()
        self.order.append(("stopped", path))
        data = self.rec.raw_bytes(path)
        return lambda: self.order.append(("rest", len(data)))


def test_finish_stops_the_recording_before_the_next_one_starts():
    """Der Befund des Reviews: liegt das Beenden in der Aktions-Queue, während
    der Aktions-Thread noch an einer Transkription sitzt, startet der Nutzer
    inzwischen das nächste Diktat — und das wartende finish() stoppt dann die
    FALSCHE Aufnahme, liest die falsche Datei und lässt die erste offen.

    Der synchrone Teil läuft deshalb auf demselben Thread wie start_recording.
    Der belegte Aktions-Thread ist hier nachgebaut, sonst wäre die Queue leer
    und die gefährliche Reihenfolge käme nie zustande."""
    order, blocker = [], threading.Event()
    rec = OrderRecorder()
    daemon = OrderDaemon(rec, order)
    listener, _ = make_listener(hold_min=0.01, on_start=daemon.start_recording,
                                on_finish=daemon.finish)
    listener.start_workers()
    try:
        kb = FakeKeyboard()
        # Aktions-Thread ist mit einem Vorgänger beschäftigt (lange Transkription)
        listener._actions.put(("busy", lambda: blocker.wait(3)))
        dictate(listener, kb)                     # Diktat 1
        assert wait_for(lambda: ("stopped", "rec.raw") in order), order
        dictate(listener, kb)                     # Diktat 2
        assert wait_for(lambda: order.count("start") == 2), order
        assert wait_for(lambda: ("stopped", "rec-b.raw") in order), order
    finally:
        blocker.set()
        listener._stop_poll.set()
    # Jede Aufnahme wurde beendet, bevor die nächste begann
    assert order.index(("stopped", "rec.raw")) < order.index("start", 1), order
    assert rec.stopped == ["rec.raw", "rec-b.raw"], rec.stopped
    assert rec.leaked == [], rec.leaked           # keine Aufnahme blieb offen


class DoubleBuffer:
    """Recorder-Attrappe mit dem Doppelpuffer: start() wechselt die Rohdatei,
    stop() gibt die eben geschlossene zurück. Der Wechsel passiert hier GENAU
    zwischen Stoppen und Auslesen — der ungünstigste Zeitpunkt, den ein
    wartendes finish() erwischen kann."""

    def __init__(self):
        self.data = {"rec.raw": b"\x01" * 200000, "rec-b.raw": b""}
        self.paths = ["rec.raw", "rec-b.raw"]
        self.raw_path = "rec.raw"
        self.started = 0.0
        self.active = True
        self.starts = 0
        self.stream_abandoned = False

    def start(self, mic="default"):
        self.starts += 1
        self.raw_path = self.paths[(self.paths.index(self.raw_path) + 1) % 2]
        return True

    def stop(self):
        path = self.raw_path
        self.start()          # das nächste Diktat beginnt in genau diesem Moment
        return path

    def raw_bytes(self, path=None):
        return self.data[path or self.raw_path]


def test_finish_reads_the_file_of_its_own_dictation(monkeypatch):
    """Der Doppelpuffer bewahrt die Daten, aber gelesen werden muss auch die
    richtige Datei: finish() nimmt den Pfad, den sein eigenes rec.stop()
    genannt hat, nicht den der inzwischen laufenden Aufnahme."""
    from quassel import daemon as daemon_mod
    rec = DoubleBuffer()
    d, seen = _rec_daemon(monkeypatch, rec)
    captured = {}
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=120: True)
    monkeypatch.setattr(daemon_mod, "wav_from_raw",
                        lambda data, path: captured.__setitem__("data", data))
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                        lambda *a, **k: "hallo welt")
    monkeypatch.setattr(daemon_mod.textproc, "postprocess",
                        lambda raw, cfg: ("text", "hallo welt"))
    monkeypatch.setattr(daemon_mod, "paste", lambda t: None)
    run_finish(d)
    assert rec.raw_path == "rec-b.raw"          # das neue Diktat läuft schon
    assert "data" in captured, seen["notes"]    # sonst: 'Aufnahme zu kurz'
    assert set(captured["data"]) == {1}         # transkribiert wurde das ALTE
    assert len(captured["data"]) > 100000


def test_cancel_recording_stops_and_checks_for_a_poisoned_stream(monkeypatch):
    """Ein Stream, der beim Abbruch aufgegeben werden musste, vergiftet den
    Prozess genauso wie einer am Diktat-Ende."""
    rec = SharedRawFile()
    rec.stream_abandoned = True
    d, seen = _rec_daemon(monkeypatch, rec)
    exits = []
    d._exit = lambda code: exits.append(code)
    d.cancel_recording("canceled_key")
    assert seen["states"] == [("idle", "")]
    assert exits == [3]


def test_finish_uses_the_streamer_it_started_with(monkeypatch):
    """Ein neues Freihand-Diktat setzt self.streamer neu, während finish noch
    läuft. Der Endtext muss trotzdem im ALTEN Streamer landen, und die
    Zwischenablage aus dessen Sicherung zurückkommen."""
    from quassel import daemon as daemon_mod
    old = SimpleNamespace(typed="", finish=lambda text: text)
    new = SimpleNamespace(typed="", finish=lambda text: pytest.fail(
        "neuer Streamer hat den alten Endtext bekommen"))
    d, seen = _rec_daemon(monkeypatch, SharedRawFile())
    d.streamer = old
    d._clip_backup = "alte-zwischenablage"
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=120: True)

    def transcribe(*a, **k):
        # genau hier startet nebenher das nächste Freihand-Diktat
        d.streamer = new
        d._clip_backup = "neue-zwischenablage"
        return "hallo welt"
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe", transcribe)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", lambda data, path: None)
    monkeypatch.setattr(daemon_mod.textproc, "postprocess",
                        lambda raw, cfg: ("text", "hallo welt"))
    run_finish(d)
    assert seen["restored"] == ["alte-zwischenablage"]
    assert d.streamer is new                # das neue Diktat bleibt unangetastet
    assert d.last_paste_len == len("hallo welt")


# ------------------------------- Selbstneustart bei verklemmtem CoreAudio

def _transcribing_daemon(monkeypatch, rec, text="hallo welt"):
    """Daemon-Attrappe, deren finish() bis zum Einfügen durchläuft."""
    from quassel import daemon as daemon_mod
    d, seen = _rec_daemon(monkeypatch, rec)
    seen["order"] = []
    d._exit = lambda code: seen["order"].append(("exit", code))
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=120: True)
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                        lambda *a, **k: text)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", lambda data, path: None)
    monkeypatch.setattr(daemon_mod.textproc, "postprocess",
                        lambda raw, cfg: ("text", text))
    monkeypatch.setattr(daemon_mod, "paste",
                        lambda t: seen["order"].append(("paste", t)))
    return d, seen


def test_finish_restarts_the_daemon_only_after_the_text_is_inserted(monkeypatch):
    """Aufgegebener Stream heißt: in CoreAudio steckt ein Mutex fest, den auch
    der nächste Stream braucht. Der Prozess muss neu starten — aber erst, wenn
    das Diktat im Fenster steht, sonst wäre es doch noch verloren."""
    from quassel import daemon as daemon_mod
    rec = SharedRawFile()
    rec.stream_abandoned = True
    d, seen = _transcribing_daemon(monkeypatch, rec)
    run_finish(d)
    assert seen["order"] == [("paste", "hallo welt"),
                             ("exit", daemon_mod.RESTART_EXIT)]
    assert seen["notes"][-1] == daemon_mod.tr("audio_restart")


def test_finish_restarts_even_on_an_early_return(monkeypatch):
    """too_short kehrt früh zurück — der Selbstneustart muss trotzdem greifen,
    sonst bliebe der vergiftete Prozess stehen."""
    from quassel import daemon as daemon_mod
    rec = SharedRawFile(data=b"")
    rec.stream_abandoned = True
    d, seen = _transcribing_daemon(monkeypatch, rec)
    run_finish(d)
    assert seen["order"] == [("exit", daemon_mod.RESTART_EXIT)]
    assert any(text == daemon_mod.tr("too_short") for text in seen["notes"])


def test_finish_does_not_restart_after_a_healthy_recording(monkeypatch):
    rec = SharedRawFile()
    d, seen = _transcribing_daemon(monkeypatch, rec)
    run_finish(d)
    assert seen["order"] == [("paste", "hallo welt")]


def test_panic_stop_restarts_after_a_poisoned_stream(monkeypatch):
    from quassel import daemon as daemon_mod
    d, seen = _panic_daemon(monkeypatch)
    d.rec = SimpleNamespace(active=True, stream_abandoned=True,
                            stop=lambda: seen["steps"].append("rec"))
    exits = []
    d._exit = lambda code: exits.append(code)
    d.panic_stop()
    assert exits == [daemon_mod.RESTART_EXIT]
    assert seen["states"] == ["idle"]        # erst aufräumen, dann beenden


# ------------------------------------------------- Not-Aus und Diagnose (Mac)

def _panic_daemon(monkeypatch, rec_stop=None, recording=True):
    """Daemon-Attrappe für panic_stop: nur die Teile, die der Not-Aus anfasst."""
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    seen = {"steps": [], "states": [], "notes": []}
    d.partial = SimpleNamespace(stop=lambda: seen["steps"].append("partial"))
    d.streamer = None
    d.rec = SimpleNamespace(active=recording,
                            stop=rec_stop or (lambda: seen["steps"].append("rec")))
    d.ducker = SimpleNamespace(restore=lambda: seen["steps"].append("ducker"))
    d.cfg = SimpleNamespace(beep=True)
    monkeypatch.setattr(daemon_mod.beep, "stop", lambda: seen["steps"].append("beep"))
    monkeypatch.setattr(daemon_mod, "state_set",
                        lambda s, text="": seen["states"].append(s))
    monkeypatch.setattr(daemon_mod, "notify",
                        lambda text, ms=4000: seen["notes"].append(text))
    monkeypatch.setattr(daemon_mod, "log", lambda m: None)
    return d, seen


def test_panic_stop_ends_recording_and_returns_to_idle(monkeypatch):
    d, seen = _panic_daemon(monkeypatch)
    d.panic_stop()
    assert seen["steps"] == ["partial", "rec", "beep", "ducker"]
    assert seen["states"] == ["idle"]
    assert d.partial is None and seen["notes"]


def test_panic_stop_survives_a_broken_recorder(monkeypatch):
    """Der Not-Aus ist die letzte Instanz: er darf an keinem Schritt scheitern,
    sonst bliebe der Zustand für immer auf 'recording' stehen."""
    def boom():
        raise RuntimeError("CoreAudio weg")
    d, seen = _panic_daemon(monkeypatch, rec_stop=boom)
    d.panic_stop()
    assert seen["states"] == ["idle"]
    assert "ducker" in seen["steps"]


def test_panic_stop_resets_the_chord_machine(monkeypatch):
    """Sonst bliebe die Maschine auf 'toggle' stehen und der nächste
    Chord-Druck liefe in ein finish() ohne Aufnahme."""
    d, seen = _panic_daemon(monkeypatch)
    resets = []
    d._listener = SimpleNamespace(force_reset=lambda: resets.append(1))
    try:
        d.panic_stop()
    finally:
        d._listener = None
    assert resets == [1]
    assert seen["states"] == ["idle"]


def test_panic_stop_without_listener_does_not_raise(monkeypatch):
    """Auf Linux (und vor dem Start des Listeners) gibt es keinen."""
    d, seen = _panic_daemon(monkeypatch)
    assert d._listener is None
    d.panic_stop()
    assert seen["states"] == ["idle"]


def test_panic_stop_reports_when_nothing_runs(monkeypatch):
    """Der Menüpunkt ist immer anklickbar. Läuft nichts, wird nichts beendet —
    aber der Nutzer bekommt eine Antwort statt vollständiger Stille."""
    from quassel import daemon as daemon_mod
    d, seen = _panic_daemon(monkeypatch, recording=False)
    d.panic_stop()
    assert seen["steps"] == [] and seen["states"] == []
    assert seen["notes"] == [daemon_mod.tr("nothing_running")]


def test_panic_stop_runs_while_only_the_transcription_is_busy(monkeypatch):
    """Aufnahme schon gestoppt, Transkription läuft noch: auch das ist ein
    laufendes Diktat."""
    d, seen = _panic_daemon(monkeypatch, recording=False)
    d._panic_flag = threading.Event()
    d._listener = SimpleNamespace(current_action="finish",
                                  force_reset=lambda: None)
    try:
        d.panic_stop()
    finally:
        d._listener = None
    assert seen["states"] == ["idle"]
    assert d._panic_flag.is_set()


def test_panic_stop_runs_during_the_synchronous_part(monkeypatch):
    """Zwischen rec.stop() und dem Start des Rests ist weder eine Aufnahme noch
    eine Aktion aktiv — trotzdem läuft dort ein Diktat, und der Not-Aus muss
    greifen statt stillschweigend nichts zu tun."""
    from quassel import daemon as daemon_mod
    d, seen = _panic_daemon(monkeypatch, recording=False)
    d._sync_action = "finish"
    d._panic_flag = threading.Event()
    d.panic_stop()
    assert seen["states"] == ["idle"]
    assert seen["notes"] == [daemon_mod.tr("panic_stopped")]
    assert d._panic_flag.is_set()


def test_panic_exit_waits_for_a_running_action(monkeypatch):
    """os._exit mitten im Einfügen wäre der einzige Pfad, auf dem der
    Selbstneustart doch noch Text verliert."""
    from quassel import daemon as daemon_mod
    d, seen = _panic_daemon(monkeypatch)
    d.rec = SimpleNamespace(active=True, stream_abandoned=True, stop=lambda: None)
    exits = []
    d._exit = lambda code: exits.append(code)
    d._listener = SimpleNamespace(current_action="finish",
                                  force_reset=lambda: None)
    monkeypatch.setattr(daemon_mod, "PANIC_EXIT_WAIT", 0.15)
    try:
        d.panic_stop()
        assert exits == []                       # Aktion lief noch
    finally:
        d._listener = None
    d._restart_if_audio_poisoned()               # nächstes Diktat-Ende holt es nach
    assert exits == [daemon_mod.RESTART_EXIT]


def test_panic_during_transcription_discards_the_text(monkeypatch):
    """Wer „Diktat sofort beenden" drückt, will keinen Text mehr im Fenster —
    auch dann nicht, wenn die Transkription noch fertig wird."""
    from quassel import daemon as daemon_mod
    d, seen = _rec_daemon(monkeypatch, SharedRawFile())
    pasted = []
    monkeypatch.setattr(daemon_mod, "paste", pasted.append)
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=600: True)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", lambda data, path: None)

    def transcribe(*a, **k):
        d._panic_flag.set()                      # Not-Aus währenddessen
        return "hallo welt"
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe", transcribe)
    monkeypatch.setattr(daemon_mod.textproc, "postprocess",
                        lambda raw, cfg: ("text", "hallo welt"))
    run_finish(d)
    assert pasted == []
    assert seen["states"][-1] == ("idle", "")


def test_a_new_dictation_does_not_undo_the_panic_of_the_previous(monkeypatch):
    """Befund des Reviews: mit EINEM Flag für alle Diktate hob der Chord-Druck
    für das nächste Diktat den Not-Aus des vorherigen auf — dessen Text kam
    danach doch noch ins Fenster, mitten in das neue Diktat hinein."""
    from quassel import daemon as daemon_mod
    d, seen = _rec_daemon(monkeypatch, SharedRawFile())
    pasted = []
    monkeypatch.setattr(daemon_mod, "paste", pasted.append)
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=600: True)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", lambda data, path: None)
    monkeypatch.setattr(daemon_mod.textproc, "postprocess",
                        lambda raw, cfg: ("text", "hallo welt"))

    def transcribe(*a, **k):
        d.panic_stop()                 # Not-Aus für DIESES Diktat
        assert d.start_recording()     # und der Nutzer diktiert sofort weiter
        return "hallo welt"
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe", transcribe)
    run_finish(d)
    assert pasted == []                # der abbestellte Text bleibt weg
    assert seen["states"][-1] == ("idle", "")


def test_sigusr2_handler_calls_panic_stop(monkeypatch):
    """Not-Aus per Signal — der einzige Weg, der ohne den Event-Tap auskommt."""
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    calls = []
    d.panic_stop = lambda *_: calls.append(1)
    handlers = {}
    monkeypatch.setattr(daemon_mod.signal, "signal",
                        lambda sig, fn: handlers.__setitem__(sig, fn))
    d._install_panic_signal()
    handler = handlers[daemon_mod.signal.SIGUSR2]
    handler(daemon_mod.signal.SIGUSR2, None)     # so ruft Python den Handler
    assert calls == [1]


def test_install_diagnostics_registers_sigusr1_only_on_mac(monkeypatch):
    from quassel import daemon as daemon_mod
    seen = []
    monkeypatch.setattr(daemon_mod.faulthandler, "enable",
                        lambda *a, **k: seen.append("enable"))
    monkeypatch.setattr(daemon_mod.faulthandler, "register",
                        lambda sig, all_threads=False: seen.append((sig, all_threads)))
    monkeypatch.setattr(daemon_mod.sys, "platform", "linux")
    daemon_mod.install_diagnostics()
    assert seen == ["enable"]                    # Linux behält SIGUSR1 unverändert
    seen.clear()
    monkeypatch.setattr(daemon_mod.sys, "platform", "darwin")
    daemon_mod.install_diagnostics()
    assert seen == ["enable", (daemon_mod.signal.SIGUSR1, True)]


def test_log_lines_carry_a_timestamp(capsys):
    """Ohne Zeitstempel ist im daemon.log weder ein Neustart noch die Dauer
    eines Hängers zu erkennen."""
    from quassel import daemon as daemon_mod
    daemon_mod.log("hallo")
    err = capsys.readouterr().err
    assert re.match(r"^\d\d:\d\d:\d\d hallo\n$", err), err


def test_warm_server_gets_only_the_short_deadline(monkeypatch):
    """War der Server in dieser Sitzung schon erreichbar, wartet am Diktat-Ende
    niemand lange auf ihn."""
    from quassel import daemon as daemon_mod
    d, seen = _rec_daemon(monkeypatch, SharedRawFile())
    deadlines = []
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=600: deadlines.append(deadline) or False)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", lambda data, path: None)
    run_finish(d)
    assert deadlines == [daemon_mod.SERVER_WAIT_FINISH]
    assert seen["states"][-1][0] == "error"


def test_cold_server_gets_the_long_deadline(monkeypatch):
    """Beim Kaltstart lädt der Server erst das Modell. Eine kurze Frist würde
    das fertige Diktat wegwerfen, statt zu warten."""
    from quassel import daemon as daemon_mod
    d, seen = _rec_daemon(monkeypatch, SharedRawFile())
    monkeypatch.setattr(daemon_mod.whisperclient, "server_was_up", lambda: False)
    deadlines = []
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=600: deadlines.append(deadline) or False)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", lambda data, path: None)
    run_finish(d)
    assert deadlines == [daemon_mod.SERVER_WAIT_COLD]
    assert daemon_mod.SERVER_WAIT_COLD > daemon_mod.SERVER_WAIT_FINISH


def test_unreachable_server_rescues_the_recording(monkeypatch, tmp_path):
    """Das Diktat ist gesprochen — es darf nicht verschwinden, nur weil der
    Server nicht kommt. Die Rohdaten werden als WAV gesichert und der Pfad
    steht in der Fehlermeldung."""
    from quassel import daemon as daemon_mod
    d, seen = _rec_daemon(monkeypatch, SharedRawFile())
    monkeypatch.setattr(daemon_mod, "RUNDIR", str(tmp_path))
    written = []
    monkeypatch.setattr(daemon_mod, "wav_from_raw",
                        lambda data, path: written.append((path, len(data))))
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=600: False)
    run_finish(d)
    assert len(written) == 1
    path, size = written[0]
    assert path.startswith(str(tmp_path)) and path.endswith(".wav")
    assert "rescued-" in path and size > 100000
    state, text = seen["states"][-1]
    assert state == "error" and path in text


def test_failed_transcription_rescues_the_recording(monkeypatch, tmp_path):
    from quassel import daemon as daemon_mod
    d, seen = _rec_daemon(monkeypatch, SharedRawFile())
    monkeypatch.setattr(daemon_mod, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server",
                        lambda deadline=600: True)
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                        lambda *a, **k: None)
    run_finish(d)
    rescued = list(tmp_path.glob("rescued-*.wav"))
    assert len(rescued) == 1, list(tmp_path.iterdir())
    assert seen["states"][-1][0] == "error"


def test_two_rescues_in_the_same_second_do_not_overwrite(monkeypatch, tmp_path):
    """Bei totem Server scheitern zwei Diktate leicht in derselben Sekunde —
    der Dateiname hat aber nur Sekundenauflösung."""
    from quassel import daemon as daemon_mod
    d, _ = _rec_daemon(monkeypatch, SharedRawFile())
    monkeypatch.setattr(daemon_mod, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(daemon_mod, "wav_from_raw",
                        lambda data, path: pathlib.Path(path).write_bytes(data))
    monkeypatch.setattr(daemon_mod.time, "strftime",
                        lambda fmt: "rescued-20260803-181500")
    first = d._rescue(b"\x01" * 100)
    second = d._rescue(b"\x02" * 100)
    assert first != second
    assert pathlib.Path(first).read_bytes() == b"\x01" * 100
    assert pathlib.Path(second).read_bytes() == b"\x02" * 100


def test_rescue_folder_keeps_only_the_last_five(monkeypatch, tmp_path):
    from quassel import daemon as daemon_mod
    d, _ = _rec_daemon(monkeypatch, SharedRawFile())
    monkeypatch.setattr(daemon_mod, "RUNDIR", str(tmp_path))
    for i in range(8):
        (tmp_path / ("rescued-20250101-00000%d.wav" % i)).write_bytes(b"alt")
    monkeypatch.setattr(daemon_mod, "wav_from_raw",
                        lambda data, path: open(path, "wb").close())
    path = d._rescue(b"\x01" * 100)
    rescued = sorted(p.name for p in tmp_path.glob("rescued-*.wav"))
    assert len(rescued) == daemon_mod.RESCUE_KEEP, rescued
    assert os.path.basename(path) in rescued        # die neueste bleibt sicher


def test_finish_reports_a_slow_recorder_stop(monkeypatch):
    """Ein träges Audiogerät ist die Hauptursache des Hängers — sie muss im
    Log stehen, sonst ist der Fall nicht zu erkennen."""
    from quassel import daemon as daemon_mod
    rec = SharedRawFile(data=b"", stop_takes=daemon_mod.SLOW_STOP + 0.05)
    d, _ = _rec_daemon(monkeypatch, rec)
    logs = []
    monkeypatch.setattr(daemon_mod, "log", logs.append)
    assert d.finish() is None                      # zu kurz -> kein Rest
    assert any("rec.stop() dauerte" in m for m in logs), logs


def test_m3_reconfigure_switches_chord_live():
    listener, ev = make_listener("ctrl+meta")
    listener.reconfigure("ctrl+alt", 0.2, 0.3)
    assert listener.machine.hold_min == 0.2
    assert listener.machine.double_window == 0.3
    kb = FakeKeyboard()
    t = 500.0
    # Neuer Chord (Strg+Alt) startet …
    process(listener, flags_item(kb, L_CTRL, True, t), now=t)
    process(listener, flags_item(kb, L_OPT, True, t), now=t)
    assert ev["start"] == 1
    process(listener, flags_item(kb, L_OPT, False, t + 1.0), now=t + 1.0)
    process(listener, flags_item(kb, L_CTRL, False, t + 1.0), now=t + 1.0)
    assert ev["finish"] == 1
    # … der alte (Strg+Cmd) nicht mehr
    process(listener, flags_item(kb, L_CTRL, True, t + 2.0), now=t + 2.0)
    process(listener, flags_item(kb, L_CMD, True, t + 2.0), now=t + 2.0)
    assert ev["start"] == 1
    process(listener, flags_item(kb, L_CMD, False, t + 2.1), now=t + 2.1)
    process(listener, flags_item(kb, L_CTRL, False, t + 2.1), now=t + 2.1)


def test_m3_chord_switch_deferred_while_recording():
    """Mitten in einer Aufnahme wechselt der Chord nicht (Zustand ginge
    verloren); Timing-Werte greifen sofort, der Chord beim nächsten Aufruf."""
    listener, ev = make_listener("ctrl+meta")
    kb = FakeKeyboard()
    t = 600.0
    process(listener, flags_item(kb, L_CTRL, True, t), now=t)
    process(listener, flags_item(kb, L_CMD, True, t), now=t)
    assert listener.machine.state == "hold"
    listener.reconfigure("ctrl+alt", 0.2, 0.3)
    assert listener.machine.b == VK_COMMAND          # Chord unverändert
    assert listener.machine.hold_min == 0.2          # Timing sofort
    process(listener, flags_item(kb, L_CMD, False, t + 1.0), now=t + 1.0)
    process(listener, flags_item(kb, L_CTRL, False, t + 1.0), now=t + 1.0)
    assert ev["finish"] == 1
    listener.reconfigure("ctrl+alt", 0.2, 0.3)       # jetzt idle -> greift
    assert listener.machine.b == VK_OPTION


# ------------------------------------------------- C2: TCC-Preflight beide Seiten

class _PermQuartz:
    def __init__(self, listen=True, post=True):
        self._listen = listen
        self._post = post
        self.requested = []

    def CGPreflightListenEventAccess(self):
        return self._listen

    def CGRequestListenEventAccess(self):
        self.requested.append("listen")
        return False

    def CGPreflightPostEventAccess(self):
        return self._post

    def CGRequestPostEventAccess(self):
        self.requested.append("post")
        return False


def _perm_setup(monkeypatch, fake):
    notes = []
    panes = []
    monkeypatch.setitem(sys.modules, "Quartz", fake)
    monkeypatch.setattr(hotkey_mac, "_notify_mac", lambda text, title="Quassel": notes.append(text))
    monkeypatch.setattr(hotkey_mac, "_open_privacy_pane", lambda anchor="": panes.append(anchor))
    return notes, panes


def test_c2_all_granted(monkeypatch):
    fake = _PermQuartz(listen=True, post=True)
    notes, panes = _perm_setup(monkeypatch, fake)
    assert check_permissions() == []
    assert notes == [] and panes == [] and fake.requested == []


def test_c2_missing_accessibility_detected_and_requested(monkeypatch):
    fake = _PermQuartz(listen=True, post=False)
    notes, panes = _perm_setup(monkeypatch, fake)
    assert check_permissions() == ["accessibility"]
    assert fake.requested == ["post"]
    assert len(notes) == 1 and "Bedienungshilfen" in notes[0]
    assert panes == ["Privacy_Accessibility"]


def test_c2_both_missing_notifies_per_grant(monkeypatch):
    fake = _PermQuartz(listen=False, post=False)
    notes, panes = _perm_setup(monkeypatch, fake)
    assert check_permissions() == ["input-monitoring", "accessibility"]
    assert fake.requested == ["listen", "post"]
    assert len(notes) == 2
    assert "Eingabeüberwachung" in notes[0] and "Bedienungshilfen" in notes[1]
    assert panes == ["Privacy_ListenEvent"]


def test_c2_no_request_skips_prompts(monkeypatch):
    fake = _PermQuartz(listen=False, post=False)
    _perm_setup(monkeypatch, fake)
    assert check_permissions(request=False) == ["input-monitoring", "accessibility"]
    assert fake.requested == []


def test_c2_post_falls_back_to_ax_prompt(monkeypatch):
    class AXQuartz:
        prompted = []

        def CGPreflightListenEventAccess(self):
            return True

        def AXIsProcessTrustedWithOptions(self, opts):
            AXQuartz.prompted.append(dict(opts))
            return False
    fake = AXQuartz()
    notes, panes = _perm_setup(monkeypatch, fake)
    assert check_permissions() == ["accessibility"]
    assert fake.prompted and list(fake.prompted[0].values()) == [True]
    assert panes == ["Privacy_Accessibility"]


def test_c2_quartz_missing_reports_both(monkeypatch):
    monkeypatch.setitem(sys.modules, "Quartz", None)   # erzwingt ImportError
    assert check_permissions() == ["input-monitoring", "accessibility"]


# ----------------------------------------------------------------- Sync-Fixes

def test_osa_quote_hardened():
    """Wie platform_mac._osa_quote: Newlines als literal \\n (rohe wären ein
    AppleScript-Syntaxfehler), Steuerzeichen raus (Tab bleibt)."""
    q = hotkey_mac._osa_quote
    assert q('a"b\\c') == '"a\\"b\\\\c"'
    assert q("zeile1\r\nzeile2\rz3\nz4") == '"zeile1\\nzeile2\\nz3\\nz4"'
    assert q("a\x07b\tc") == '"ab\tc"'


def test_start_recording_failure_names_missing_piece(monkeypatch):
    """Die Fehlermeldung nennt, was tatsächlich fehlt — auf dem Mac hängt das
    am Aufnahmebackend: beim sounddevice-Pfad fehlt kein Programm, sondern ein
    nutzbares Eingabegerät bzw. die Mikrofon-Freigabe."""
    from quassel import daemon as daemon_mod
    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    d.cfg = SimpleNamespace(reload=lambda: False, ui_language="auto", mic="default")
    d.rec = SimpleNamespace(start=lambda mic: False)
    notes = []
    monkeypatch.setattr(daemon_mod, "notify", lambda text, ms=0: notes.append(text))
    monkeypatch.setattr(daemon_mod.sys, "platform", "darwin")
    monkeypatch.delenv("QUASSEL_MAC_AUDIO", raising=False)
    assert d.start_recording() is False
    assert notes == ["Fehler: Mikrofonzugriff fehlt"]
    notes.clear()
    monkeypatch.setenv("QUASSEL_MAC_AUDIO", "ffmpeg")
    assert d.start_recording() is False
    assert notes == ["Fehler: ffmpeg fehlt"]
    notes.clear()
    monkeypatch.setattr(daemon_mod.sys, "platform", "linux")
    assert d.start_recording() is False
    assert notes == ["Fehler: pw-record/parecord fehlt"]
