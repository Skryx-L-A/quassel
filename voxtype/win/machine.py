"""Plattformunabhängige Chord-Zustandsmaschine (Halten / Doppeltipp).

Dieselbe Logik wie im Linux-evdev-Daemon, aber als reine Funktion von
Tastenereignissen — damit sie sich auf Windows über den keyboard-Hook
testen und wiederverwenden lässt. Sie ruft Callbacks für die vier
Übergänge auf: on_start, on_finish, on_cancel, on_handsfree.
"""
import time


class ChordMachine:
    def __init__(self, group_a, group_b, on_start, on_finish, on_cancel,
                 hold_min=0.5, double_window=0.45):
        self.a, self.b = set(group_a), set(group_b)
        self.on_start = on_start          # Aufnahme beginnen
        self.on_finish = on_finish        # Aufnahme beenden + einfügen
        self.on_cancel = on_cancel        # Aufnahme verwerfen (reason-key)
        self.hold_min = hold_min
        self.double_window = double_window
        self.pressed = set()
        self.state = "idle"               # idle|hold|await2|toggle_armed|toggle
        self.t_chord = 0.0
        self.t_tap = 0.0
        self.pending_finish = False

    def _chord_down(self):
        return bool(self.pressed & self.a) and bool(self.pressed & self.b)

    def key(self, vk, pressed, now=None):
        now = now if now is not None else time.monotonic()
        is_mod = vk in self.a or vk in self.b
        if is_mod:
            before = self._chord_down()
            if pressed:
                self.pressed.add(vk)
            else:
                self.pressed.discard(vk)
            chord = self._chord_down()
            if chord and not before:
                if self.state == "idle":
                    self.state = "hold"
                    self.t_chord = now
                    self.on_start()
                elif self.state == "await2":
                    self.state = "toggle_armed"
                elif self.state == "toggle":
                    self.state = "idle"
                    self.pending_finish = True
            elif before and not chord:
                if self.state == "hold":
                    if now - self.t_chord >= self.hold_min:
                        self.state = "idle"
                        self.pending_finish = True
                    else:
                        self.state = "await2"
                        self.t_tap = now
                elif self.state == "toggle_armed":
                    self.state = "toggle"
        else:
            if pressed and self.state in ("hold", "toggle_armed"):
                self.state = "idle"
                self.on_cancel("canceled_key")

    def poll(self, now=None):
        """Regelmäßig aufrufen: Timeouts + ausstehendes Einfügen abwickeln."""
        now = now if now is not None else time.monotonic()
        if self.state == "await2" and now - self.t_tap > self.double_window:
            self.state = "idle"
            self.on_cancel("canceled_tap")
        if self.pending_finish and not self.pressed:
            self.pending_finish = False
            self.on_finish()
