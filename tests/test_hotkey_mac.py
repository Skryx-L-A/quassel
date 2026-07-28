"""Tests der mac-Event-Zuordnung (hotkey_mac.py) ohne echtes Quartz/pyobjc.

FakeKeyboard erzeugt (Keycode, Flag-Masken)-Paare exakt so, wie macOS sie
liefert: SEITEN-Keycode, aber nur ein FAMILIEN-Bit — d.h. das Loslassen
einer Seite, während die andere hält, kommt mit GESETZTEM Bit an. Damit
sind die Tests echte Verhaltenstests gegen die reale Event-Semantik,
keine Mock-Echos."""
import os
import sys
import threading
import time
from types import SimpleNamespace

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


def test_listener_hold_to_talk_via_process():
    listener, ev = make_listener()
    kb = FakeKeyboard()
    t = 100.0
    listener._process(flags_item(kb, L_CTRL, True, t), now=t)
    listener._process(flags_item(kb, L_CMD, True, t), now=t)
    assert ev["start"] == 1
    listener._process(flags_item(kb, L_CMD, False, t + 1.0), now=t + 1.0)
    listener._process(flags_item(kb, L_CTRL, False, t + 1.0), now=t + 1.0)
    assert ev["finish"] == 1 and not ev["cancel"], ev


def test_listener_c1_scenario_via_process():
    """C1-Szenario über den kompletten Listener-Pfad (Queue-Items + _process)."""
    listener, ev = make_listener()
    kb = FakeKeyboard()
    t = 200.0
    listener._process(flags_item(kb, L_CTRL, True, t), now=t)
    listener._process(flags_item(kb, L_CMD, True, t), now=t)
    listener._process(flags_item(kb, R_CTRL, True, t + 0.2), now=t + 0.2)
    listener._process(flags_item(kb, R_CTRL, False, t + 0.3), now=t + 0.3)
    listener._process(flags_item(kb, L_CMD, False, t + 1.0), now=t + 1.0)
    listener._process(flags_item(kb, L_CTRL, False, t + 1.0), now=t + 1.0)
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


def test_h3_long_finish_never_interleaves_with_start():
    """Ein blockierendes finish() (Transkription) darf nicht mit einem neuen
    start() verschränken: beide laufen auf demselben Worker-Thread."""
    order = []
    finish_began = threading.Event()
    second_start = threading.Event()

    def on_start():
        order.append("start")
        if len([x for x in order if x == "start"]) == 2:
            second_start.set()
        return True

    def on_finish():
        order.append("finish-begin")
        finish_began.set()
        time.sleep(0.3)                       # simulierte lange Transkription
        order.append("finish-end")

    listener, _ = make_listener(hold_min=0.01, on_start=on_start, on_finish=on_finish)
    worker = threading.Thread(target=listener._worker, daemon=True)
    worker.start()
    try:
        kb = FakeKeyboard()
        listener._events.put(flags_item(kb, L_CTRL, True, time.monotonic()))
        listener._events.put(flags_item(kb, L_CMD, True, time.monotonic()))
        time.sleep(0.05)                      # > hold_min
        listener._events.put(flags_item(kb, L_CMD, False, time.monotonic()))
        listener._events.put(flags_item(kb, L_CTRL, False, time.monotonic()))
        assert finish_began.wait(2), "finish nie gestartet"
        # Während finish schläft: neuer Chord-Druck trifft ein
        listener._events.put(flags_item(kb, L_CTRL, True, time.monotonic()))
        listener._events.put(flags_item(kb, L_CMD, True, time.monotonic()))
        assert second_start.wait(2), "zweiter start nie gefeuert: %s" % order
    finally:
        listener._stop_poll.set()
        worker.join(2)
    assert order == ["start", "finish-begin", "finish-end", "start"], order


def test_m2_force_finish_ends_handsfree():
    listener, ev = make_listener()
    kb = FakeKeyboard()
    t = 300.0
    # Doppeltipp -> Freihand (toggle)
    listener._process(flags_item(kb, L_CTRL, True, t), now=t)
    listener._process(flags_item(kb, L_CMD, True, t), now=t)
    listener._process(flags_item(kb, L_CMD, False, t + 0.1), now=t + 0.1)
    listener._process(flags_item(kb, L_CTRL, False, t + 0.1), now=t + 0.1)
    listener._process(flags_item(kb, L_CTRL, True, t + 0.3), now=t + 0.3)
    listener._process(flags_item(kb, L_CMD, True, t + 0.3), now=t + 0.3)
    listener._process(flags_item(kb, L_CMD, False, t + 0.4), now=t + 0.4)
    listener._process(flags_item(kb, L_CTRL, False, t + 0.4), now=t + 0.4)
    assert listener.machine.state == "toggle" and ev["handsfree"] == 1
    assert listener.force_finish() is True
    listener._process(None, now=t + 0.5)      # nächster Poll-Tick feuert finish
    assert ev["finish"] == 1
    assert listener.machine.state == "idle" and not listener.machine.pending_finish


def test_m2_force_finish_noop_outside_handsfree():
    listener, ev = make_listener()
    kb = FakeKeyboard()
    assert listener.force_finish() is False   # idle
    t = 400.0
    listener._process(flags_item(kb, L_CTRL, True, t), now=t)
    listener._process(flags_item(kb, L_CMD, True, t), now=t)
    assert listener.machine.state == "hold"
    assert listener.force_finish() is False   # Halten-Modus: kein Limit (wie Linux)
    assert ev["finish"] == 0


def test_m2_daemon_watchdog_triggers_after_max_record():
    from quassel.daemon import Daemon, MAX_RECORD
    d = Daemon.__new__(Daemon)                # __init__ (Cfg/Recorder) umgehen
    d.rec = SimpleNamespace(active=True, started=100.0)
    calls = []
    listener = SimpleNamespace(force_finish=lambda: calls.append(1) or True)
    d._mac_watchdog(listener, now=100.0 + MAX_RECORD - 1)
    assert calls == []                        # unter dem Limit
    d._mac_watchdog(listener, now=100.0 + MAX_RECORD + 1)
    assert calls == [1]
    d.rec = SimpleNamespace(active=False, started=100.0)
    d._mac_watchdog(listener, now=100.0 + MAX_RECORD + 1)
    assert calls == [1]                       # keine aktive Aufnahme -> nichts


def test_m3_reconfigure_switches_chord_live():
    listener, ev = make_listener("ctrl+meta")
    listener.reconfigure("ctrl+alt", 0.2, 0.3)
    assert listener.machine.hold_min == 0.2
    assert listener.machine.double_window == 0.3
    kb = FakeKeyboard()
    t = 500.0
    # Neuer Chord (Strg+Alt) startet …
    listener._process(flags_item(kb, L_CTRL, True, t), now=t)
    listener._process(flags_item(kb, L_OPT, True, t), now=t)
    assert ev["start"] == 1
    listener._process(flags_item(kb, L_OPT, False, t + 1.0), now=t + 1.0)
    listener._process(flags_item(kb, L_CTRL, False, t + 1.0), now=t + 1.0)
    assert ev["finish"] == 1
    # … der alte (Strg+Cmd) nicht mehr
    listener._process(flags_item(kb, L_CTRL, True, t + 2.0), now=t + 2.0)
    listener._process(flags_item(kb, L_CMD, True, t + 2.0), now=t + 2.0)
    assert ev["start"] == 1
    listener._process(flags_item(kb, L_CMD, False, t + 2.1), now=t + 2.1)
    listener._process(flags_item(kb, L_CTRL, False, t + 2.1), now=t + 2.1)


def test_m3_chord_switch_deferred_while_recording():
    """Mitten in einer Aufnahme wechselt der Chord nicht (Zustand ginge
    verloren); Timing-Werte greifen sofort, der Chord beim nächsten Aufruf."""
    listener, ev = make_listener("ctrl+meta")
    kb = FakeKeyboard()
    t = 600.0
    listener._process(flags_item(kb, L_CTRL, True, t), now=t)
    listener._process(flags_item(kb, L_CMD, True, t), now=t)
    assert listener.machine.state == "hold"
    listener.reconfigure("ctrl+alt", 0.2, 0.3)
    assert listener.machine.b == VK_COMMAND          # Chord unverändert
    assert listener.machine.hold_min == 0.2          # Timing sofort
    listener._process(flags_item(kb, L_CMD, False, t + 1.0), now=t + 1.0)
    listener._process(flags_item(kb, L_CTRL, False, t + 1.0), now=t + 1.0)
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
