"""macOS-Tastatur-Listener: Modifier-Chord (Standard Strg+Cmd) über CGEventTap.

Speist dieselbe plattformneutrale Zustandsmaschine (quassel.win.machine.
ChordMachine) wie der Windows-Raw-Input-Hook — Halten = Push-to-Talk,
Doppeltipp = Freihand, andere Taste während des Chords = Abbruch.

Events lesen erfordert unter macOS 10.15+ "Eingabeüberwachung" (Input
Monitoring); Tasten SENDEN (CGEventPost, das Einfügen per Cmd+V) zusätzlich
"Bedienungshilfen" (Accessibility) — ohne die verwirft macOS die Events
still. check_permissions prüft beide, fordert sie an und meldet, was fehlt;
der Daemon degradiert (Log + Benachrichtigung), statt abzustürzen.

kCGEventFlagsChanged liefert einen SEITEN-Keycode (0x3B = linkes Strg,
0x3E = rechtes Strg), aber nur ein FAMILIEN-Flag-Bit (MaskControl). Ein
Event mit gesetztem Bit ist daher ein Umschalter: Keycode noch nicht
gedrückt = Druck; Keycode bereits gedrückt = Loslassen dieser Seite,
während die andere noch hält. Bit gelöscht = alle Seiten der Familie los.
decode_flags_changed bildet das auf (Keycode, gedrückt)-Paare ab.

Drei Threads, streng getrennt, damit kein langsamer Aufruf den Hotkey tötet:

  Tap-Thread      macOS liefert die Events; der Callback reiht sie nur ein
                  (sonst deaktiviert macOS den Tap wegen Timeout). Wird der
                  Tap doch deaktiviert (Timeout/Secure Input), reaktiviert
                  ihn der Callback selbst.
  Ereignis-Thread ("mac-hotkey-worker") treibt die ChordMachine (key/poll
                  unter dem Lock) und führt danach den SYNCHRONEN Teil der
                  fälligen Aktionen selbst aus: on_start, und von finish/
                  cancel alles, was die Aufnahme betrifft (stoppen, Rohdaten
                  lesen). Damit liegt das Beenden einer Aufnahme immer vor dem
                  Start der nächsten — dieselbe Zusicherung wie vor der
                  Aufteilung, und keine, die ein Schloss herstellen müsste.
  Aktions-Thread  ("mac-hotkey-actions") führt aus, was eine Aktion als
                  Callable zurückgibt: Transkription, Nachbearbeitung,
                  Einfügen. Diese Teile laufen der Reihe nach und fassen die
                  Aufnahme nicht mehr an; hängt einer, bleiben Tastendrücke
                  und Poll-Ticks trotzdem bedient.

Beide eigenen Threads führen einen Zeitstempel mit (last_event_tick,
last_action_tick) und den Namen dessen, woran sie gerade arbeiten
(current_sync für den synchronen Teil, current_action für den Rest): daran
erkennt der Watchdog im Daemon einen wirklich stehenden Ereignis-Thread und
unterscheidet ihn von einem, der nur zäh arbeitet.

CHORDS aus config.py sind evdev-Keycodes und hier nicht nutzbar; MAC_CHORDS
bildet dieselben Chord-Namen auf macOS-Virtual-Keycodes ab (je linke/rechte
Taste, wie beim Windows-Hook in quassel/win/hook.py).

Die Event-Auswertung (decode_flags_changed / handle_flags_changed /
handle_key_down) ist bewusst von Quartz entkoppelt: reine Funktionen auf
(Keycode, Flag-Zustand) -> ChordMachine, damit sie ohne pyobjc per
Unit-Test prüfbar sind.
"""
import queue
import subprocess
import sys
import threading
import time

from .win.machine import ChordMachine

# macOS Virtual-Keycodes (Carbon/HIToolbox kVK_*), links + rechts getrennt
VK_CONTROL = {0x3B, 0x3E}   # kVK_Control, kVK_RightControl
VK_COMMAND = {0x37, 0x36}   # kVK_Command, kVK_RightCommand
VK_OPTION = {0x3A, 0x3D}    # kVK_Option, kVK_RightOption
VK_SHIFT = {0x38, 0x3C}     # kVK_Shift, kVK_RightShift
VK_FN = {0x3F}              # kVK_Function
VK_CAPSLOCK = {0x39}        # kVK_CapsLock

# Alle Modifier, die als kCGEventFlagsChanged (nie als KeyDown) ankommen.
# Shift/Fn/CapsLock gehören zu keinem Chord — ihr Druck während eines
# armierten Chords bricht ab (Parität zu Linux: andere Taste = Abbruch).
_FAMILIES = {
    "control": VK_CONTROL,
    "command": VK_COMMAND,
    "option": VK_OPTION,
    "shift": VK_SHIFT,
    "fn": VK_FN,
    "capslock": VK_CAPSLOCK,
}

MAC_CHORDS = {
    "ctrl+meta": (VK_CONTROL, VK_COMMAND),
    "alt+meta":  (VK_OPTION, VK_COMMAND),
    "ctrl+alt":  (VK_CONTROL, VK_OPTION),
}

POLL_INTERVAL = 0.05   # s: await2-Timeout + pending_finish abwickeln (wie daemon.py)
LOCK_WAIT = 1.0        # s: so lange wartet force_finish auf den Maschinen-Lock


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _osa_quote(s):
    """AppleScript-Stringliteral: \\ und " escapen, Zeilenumbrüche als \\n
    (rohe Newlines sind im Literal ein Syntaxfehler -> Notification ginge
    stumm verloren), übrige Steuerzeichen entfernen."""
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    s = "".join(ch for ch in s if ch >= " " or ch == "\t")
    return f'"{s}"'


def _notify_mac(text, title="Quassel"):
    """Systembenachrichtigung über osascript (kein Extra-Paket nötig)."""
    script = "display notification %s with title %s" % (_osa_quote(text), _osa_quote(title))
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def _open_privacy_pane(anchor="Privacy_ListenEvent"):
    try:
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?" + anchor],
            check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def _ax_trusted(request):
    """Accessibility-Vertrauensstatus über die AX-API (Fallback, wenn die
    CGPreflight-Funktionen fehlen). request=True zeigt den System-Prompt
    (AXIsProcessTrustedWithOptions). None = API nicht verfügbar."""
    for mod_name in ("Quartz", "ApplicationServices"):
        try:
            mod = __import__(mod_name)
        except ImportError:
            continue
        if request and hasattr(mod, "AXIsProcessTrustedWithOptions"):
            key = getattr(mod, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt")
            try:
                return bool(mod.AXIsProcessTrustedWithOptions({key: True}))
            except Exception:    # noqa: BLE001 — Preflight darf nie crashen
                pass
        if hasattr(mod, "AXIsProcessTrusted"):
            return bool(mod.AXIsProcessTrusted())
    return None


def check_permissions(request=True):
    """Liste fehlender TCC-Freigaben (leer = alles erteilt):
      'input-monitoring'  — Events LESEN (CGEventTap, der Diktier-Hotkey)
      'accessibility'     — Events SENDEN (CGEventPost; ohne sie verwirft
                            macOS das Cmd+V beim Einfügen still)
    Fehlende Freigaben werden (falls request) angefordert, je Freigabe wird
    benachrichtigt und die passende Systemeinstellungs-Seite geöffnet.
    Stürzt nie ab (degradiert nur)."""
    try:
        import Quartz
    except ImportError:
        _log("mac-hotkey: pyobjc/Quartz fehlt -> Tastatur-Listener deaktiviert")
        return ["input-monitoring", "accessibility"]

    missing = []

    # Lesen (Eingabeüberwachung)
    if hasattr(Quartz, "CGPreflightListenEventAccess"):
        listen = bool(Quartz.CGPreflightListenEventAccess())
    else:
        trusted = _ax_trusted(request=False)
        if trusted is None:
            _log("mac-hotkey: keine TCC-Preflight-API (Lesen) -> versuche es trotzdem")
        listen = True if trusted is None else trusted
    if not listen:
        missing.append("input-monitoring")
        if request and hasattr(Quartz, "CGRequestListenEventAccess"):
            Quartz.CGRequestListenEventAccess()
        _log("mac-hotkey: Berechtigung 'Eingabeüberwachung' fehlt -> Chord-Hotkey inaktiv")
        _notify_mac(
            "Quassel braucht die Berechtigung „Eingabeüberwachung“, um den "
            "Diktier-Hotkey (Strg+Cmd) zu erkennen. Bitte in den Systemeinstellungen "
            "unter Datenschutz & Sicherheit → Eingabeüberwachung freigeben und "
            "Quassel danach neu starten.")

    # Senden (Bedienungshilfen)
    if hasattr(Quartz, "CGPreflightPostEventAccess"):
        post = bool(Quartz.CGPreflightPostEventAccess())
        if not post and request and hasattr(Quartz, "CGRequestPostEventAccess"):
            Quartz.CGRequestPostEventAccess()
    else:
        trusted = _ax_trusted(request=request)
        if trusted is None:
            _log("mac-hotkey: keine TCC-Preflight-API (Senden) -> versuche es trotzdem")
        post = True if trusted is None else trusted
    if not post:
        missing.append("accessibility")
        _log("mac-hotkey: Berechtigung 'Bedienungshilfen' fehlt -> Einfügen "
             "(Cmd+V) würde von macOS still verworfen")
        _notify_mac(
            "Quassel braucht die Berechtigung „Bedienungshilfen“, um diktierten "
            "Text einzufügen. Bitte in den Systemeinstellungen unter Datenschutz "
            "& Sicherheit → Bedienungshilfen freigeben und Quassel danach neu starten.")

    if "input-monitoring" in missing:
        _open_privacy_pane("Privacy_ListenEvent")
    elif "accessibility" in missing:
        _open_privacy_pane("Privacy_Accessibility")
    return missing


def _family(keycode):
    for name, keys in _FAMILIES.items():
        if keycode in keys:
            return name
    return None


def decode_flags_changed(down, keycode, flag_masks):
    """Ein kCGEventFlagsChanged-Ereignis in (Keycode, gedrückt)-Paare übersetzen.

    down: Menge der aktuell gedrückten Modifier-SEITEN-Keycodes (wird
    fortgeschrieben). flag_masks: {'control': bool, ...} — ob das Familien-Bit
    in den CGEvent-Flags gesetzt ist.

    Bit gesetzt  -> Umschalter: Keycode nicht in down = Druck dieser Seite;
                    Keycode in down = Loslassen dieser Seite (die ANDERE Seite
                    hält das Bit noch).
    Bit gelöscht -> alle gedrückten Seiten dieser Familie sind los.
    CapsLock ist besonders: das Bit spiegelt den Feststell-ZUSTAND, nicht die
    Taste — jedes Event zählt als Druck."""
    family = _family(keycode)
    if family is None:
        return []
    if family == "capslock":
        return [(keycode, True)]
    if flag_masks.get(family):
        if keycode in down:
            down.discard(keycode)
            return [(keycode, False)]
        down.add(keycode)
        return [(keycode, True)]
    released = [k for k in _FAMILIES[family] if k in down]
    down.difference_update(released)
    if keycode not in released:
        released.append(keycode)
    return [(k, False) for k in released]


def handle_flags_changed(machine, down, keycode, flag_masks, now=None):
    """Dekodierte Modifier-Ereignisse in die ChordMachine speisen. down ist
    der geteilte Seiten-Zustand (siehe decode_flags_changed). Shift/Fn/
    CapsLock sind keine Chord-Tasten -> ihr Druck läuft in den else-Zweig
    der Maschine und bricht einen armierten Chord ab."""
    for k, pressed in decode_flags_changed(down, keycode, flag_masks):
        machine.key(k, pressed, now)


def handle_key_down(machine, keycode, now=None):
    """Normale (Nicht-Modifier-)Taste während des Chords -> Abbruch, wie bei
    evdev/Windows. Modifier-Keycodes hier ignorieren (die laufen über
    handle_flags_changed und würden sonst doppelt gezählt)."""
    if _family(keycode) is not None:
        return
    machine.key(keycode, True, now)


class MacHotkeyListener(threading.Thread):
    """CGEventTap + CFRunLoop im eigenen Thread; der Tap-Callback reiht nur
    ein, der Ereignis-Thread treibt die ChordMachine (key/poll unter Lock),
    der Aktions-Thread führt finish/cancel/handsfree aus."""

    def __init__(self, chord_name, cfg, on_start, on_finish, on_cancel, on_handsfree=None):
        super().__init__(daemon=True, name="mac-hotkey")
        group_a, group_b = MAC_CHORDS.get(chord_name, MAC_CHORDS["ctrl+meta"])
        self._lock = threading.Lock()
        self._events = queue.Queue()
        self._actions = queue.Queue()   # (Name, Callable) für den Aktions-Thread
        self._mod_down = set()      # gedrückte Modifier-Seiten (decode_flags_changed)
        self._pending_cb = []       # Aktionen, die nach dem Lock-Release fällig sind
        # Lebenszeichen beider Threads, vom Watchdog im Daemon gelesen.
        self.last_event_tick = time.monotonic()
        self.last_action_tick = time.monotonic()
        self.current_action = None  # Name der laufenden Aktion (None = frei)
        self.current_sync = None    # Name des laufenden SYNCHRONEN Teils
        # on_start bleibt synchron auf dem Ereignis-Thread: sein Rückgabewert
        # entscheidet, ob die Maschine armiert (fehlgeschlagener Aufnahme-Start
        # darf nicht in 'hold' führen). Alles andere geht an den Aktions-Thread.
        self.machine = ChordMachine(
            group_a, group_b,
            on_start,
            lambda: self._pending_cb.append(("finish", on_finish)),
            lambda r: self._pending_cb.append(("cancel", lambda: on_cancel(r))),
            hold_min=cfg.hold_min, double_window=cfg.double_window)
        if on_handsfree is not None:
            self.machine.on_handsfree = lambda: self._pending_cb.append(
                ("handsfree", on_handsfree))
        self._tap = None
        self._cfrunloop = None
        self._stop_poll = threading.Event()
        self.ok = False

    def run(self):
        try:
            import Quartz
        except ImportError:
            _log("mac-hotkey: Quartz fehlt -> Listener läuft nicht")
            return

        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown))
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly, mask, self._callback, None)
        if tap is None:
            _log("mac-hotkey: CGEventTapCreate fehlgeschlagen (Berechtigung fehlt?)")
            return

        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._cfrunloop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._cfrunloop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self.ok = True

        self.start_workers()
        Quartz.CFRunLoopRun()

    def start_workers(self):
        """Ereignis- und Aktions-Thread starten (getrennt, siehe Modulkopf)."""
        threading.Thread(target=self._worker, daemon=True,
                         name="mac-hotkey-worker").start()
        threading.Thread(target=self._action_worker, daemon=True,
                         name="mac-hotkey-actions").start()

    def _callback(self, proxy, etype, event, refcon):
        """Läuft auf dem Tap-Thread: NUR einreihen (+ Tap-Reaktivierung),
        keine Maschinen- oder Daemon-Arbeit — sonst deaktiviert macOS den
        Tap wegen Timeout."""
        import Quartz
        if etype in (Quartz.kCGEventTapDisabledByTimeout,
                     Quartz.kCGEventTapDisabledByUserInput):
            _log("mac-hotkey: Event-Tap deaktiviert (type=%s) -> reaktivieren" % etype)
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event
        now = time.monotonic()
        keycode = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
        if etype == Quartz.kCGEventFlagsChanged:
            flags = Quartz.CGEventGetFlags(event)
            masks = {
                "control": bool(flags & Quartz.kCGEventFlagMaskControl),
                "command": bool(flags & Quartz.kCGEventFlagMaskCommand),
                "option": bool(flags & Quartz.kCGEventFlagMaskAlternate),
                "shift": bool(flags & Quartz.kCGEventFlagMaskShift),
                "fn": bool(flags & Quartz.kCGEventFlagMaskSecondaryFn),
                "capslock": bool(flags & Quartz.kCGEventFlagMaskAlphaShift),
            }
            self._events.put(("flags", keycode, masks, now))
        elif etype == Quartz.kCGEventKeyDown:
            self._events.put(("down", keycode, now))
        return event

    def _worker(self):
        while not self._stop_poll.is_set():
            try:
                item = self._events.get(timeout=POLL_INTERVAL)
            except queue.Empty:
                item = None
            self._process(item)

    def _process(self, item, now=None):
        """Ein eingereihtes Event (oder None = Poll-Tick) abwickeln. key/poll
        laufen unter dem Lock; die fällig gewordenen Aktionen führt dieser
        Thread danach selbst aus. Gibt eine davon ein Callable zurück, ist das
        ihr langer Rest — er geht an den Aktions-Thread, damit hier nichts
        wartet."""
        self.last_event_tick = time.monotonic()
        with self._lock:
            if item is not None:
                if item[0] == "flags":
                    handle_flags_changed(self.machine, self._mod_down,
                                         item[1], item[2], item[3])
                else:
                    handle_key_down(self.machine, item[1], item[2])
            self.machine.poll(now)
            cbs = self._pending_cb
            self._pending_cb = []
        for name, cb in cbs:
            # current_sync sagt dem Watchdog, dass dieser Thread arbeitet und
            # nicht hängt: der synchrone Teil darf zäh sein (Gerät stoppen,
            # osascript), er wird deshalb an der langen Frist gemessen.
            self.current_sync = name
            try:
                rest = cb()
            except Exception as exc:   # noqa: BLE001 — dieser Thread darf nie sterben
                _log("mac-hotkey: Aktion %r fehlgeschlagen: %r" % (name, exc))
                continue
            finally:
                self.current_sync = None
                self.last_event_tick = time.monotonic()
            if callable(rest):
                self._actions.put((name, rest))
        self.last_event_tick = time.monotonic()

    def _action_worker(self):
        """Die langen Reste der Aktionen der Reihe nach ausführen. Dauert einer
        (Transkription) oder hängt er ganz, sieht der Watchdog das an
        current_action + last_action_tick — der Ereignis-Thread merkt nichts."""
        while not self._stop_poll.is_set():
            try:
                name, cb = self._actions.get(timeout=POLL_INTERVAL)
            except queue.Empty:
                self.last_action_tick = time.monotonic()
                continue
            self.current_action = name
            self.last_action_tick = time.monotonic()
            # Eine geworfene Ausnahme darf den Thread nicht töten — sonst
            # stünde nach einem Fehler in finish() jedes weitere Diktat still.
            try:
                cb()
            except Exception as exc:   # noqa: BLE001
                _log("mac-hotkey: Aktion %r fehlgeschlagen: %r" % (name, exc))
            finally:
                self.current_action = None
                self.last_action_tick = time.monotonic()

    def reconfigure(self, chord_name, hold_min, double_window):
        """Geänderte Hotkey-Einstellungen live übernehmen (ohne Neustart).
        Der Chord-Wechsel greift nur im Ruhezustand — mitten in einer
        Aufnahme würde die Maschine ihren Zustand verlieren; der nächste
        Aufruf (Housekeeping-Loop) holt ihn nach."""
        group_a, group_b = MAC_CHORDS.get(chord_name, MAC_CHORDS["ctrl+meta"])
        # Aufrufer ist die Haushaltsschleife, die auch den Watchdog fährt: sie
        # darf hier nicht festhängen, sonst bliebe der Hänger unbemerkt. Sie
        # kommt in fünf Sekunden ohnehin wieder vorbei.
        if not self._lock.acquire(timeout=LOCK_WAIT):
            _log("mac: Hotkey-Lock blockiert -> Einstellungen greifen später")
            return
        try:
            m = self.machine
            m.hold_min = hold_min
            m.double_window = double_window
            if (m.a, m.b) != (group_a, group_b) and m.state == "idle" \
                    and not m.pending_finish:
                m.a, m.b = set(group_a), set(group_b)
                m.pressed.clear()
        finally:
            self._lock.release()

    def force_finish(self):
        """Freihand-Aufnahme hart beenden (MAX_RECORD-Sicherheitslimit).
        True = ausgelöst; das on_finish feuert über den nächsten Poll-Tick
        des Ereignis-Threads. False = nicht ausgelöst: entweder läuft gar
        keine Freihand-Aufnahme, oder der Lock ist blockiert (hängendes
        on_start) — dann muss der Aufrufer zum Not-Aus eskalieren."""
        if not self._lock.acquire(timeout=LOCK_WAIT):
            _log("mac: Hotkey-Lock blockiert -> force_finish nicht möglich")
            return False
        try:
            if self.machine.state not in ("toggle", "toggle_armed"):
                return False
            self.machine.state = "idle"
            self.machine.pressed.clear()
            self.machine.pending_finish = True
        finally:
            self._lock.release()
        return True

    def force_reset(self):
        """Maschine hart in den Ruhezustand zwingen — nach dem Not-Aus, wenn
        sie sonst auf "toggle" stehenbliebe und der nächste Chord-Druck in ein
        finish() ohne Aufnahme liefe."""
        got = self._lock.acquire(timeout=LOCK_WAIT)
        if not got:
            _log("mac: Hotkey-Lock blockiert -> Zustand wird ohne Lock zurückgesetzt")
        try:
            # Bewusst AUCH ohne Lock: der Not-Aus ist der letzte Ausweg, und ein
            # Wettlauf beim Zurücksetzen ist besser als ein Hotkey, der für den
            # Rest der Sitzung verklemmt bleibt.
            self.machine.state = "idle"
            self.machine.pressed.clear()
            self.machine.pending_finish = False
        finally:
            if got:
                self._lock.release()

    def stop(self):
        self._stop_poll.set()
        if self._cfrunloop is not None:
            import Quartz
            Quartz.CFRunLoopStop(self._cfrunloop)
