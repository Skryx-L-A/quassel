"""macOS-Adapter: Einfügen (Clipboard + Cmd+V) und Tasten senden.

Das Einfügen über die Zwischenablage ist layout-sicher und funktioniert auch
in Terminals. Tastenereignisse laufen über Quartz (CGEvent), das keine
Accessibility/Input-Monitoring-Sonderrechte für reines Senden braucht, wohl
aber für globale Hotkeys (siehe hook.py-Äquivalent).
"""
import ctypes
import subprocess
import threading
import time

try:
    from AppKit import NSPasteboard, NSPasteboardItem, NSStringPboardType
    from Foundation import NSData
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        CGEventKeyboardSetUnicodeString,
        kCGHIDEventTap,
        kCGEventFlagMaskCommand,
    )
    _HAS_OBJC = True
except ImportError:      # pyobjc fehlt (z.B. Test-Umgebung ohne mac-Frameworks)
    _HAS_OBJC = False

KEY_V = 9
KEY_BACKSPACE = 51
KEY_ENTER = 36

# Marker für Clipboard-Manager/Universal Clipboard: eigene (Diktat-)Einträge
# überspringen — Transkripte sollen nicht in Historien/aufs iPhone wandern.
CONCEALED_TYPE = "org.nspasteboard.ConcealedType"

# Timing (Modul-Konstanten, in Tests verkürzbar). Großzügige Restore-Wartezeit:
# manche Apps holen den Clipboard-Inhalt verzögert ab — zu frühes
# Wiederherstellen fügt sonst den ALTEN Inhalt ein.
PASTE_SETTLE = 0.25
RESTORE_DELAY = 6.0
STREAM_RESTORE_DELAY = 2.0

# Frist für JEDEN osascript-Aufruf (Notification, Ducking). Ohne sie hängt der
# aufrufende Thread unbegrenzt, wenn das Notification Center oder ein Player
# klemmt — auf dem Mac ist das der Thread, der den Hotkey bedient.
OSA_TIMEOUT = 5

# EIN Restore-Slot für alle Einfüge-Pfade, lock-geschützt: ein neuer
# paste()/type_chunk() bricht den anstehenden Restore ab und trägt das
# ORIGINAL des Nutzers weiter — nie einen eigenen früheren Paste (sonst
# landet am Ende das rohe Diktat dauerhaft in der Zwischenablage).
_clip_lock = threading.Lock()
_restore_timer = None      # anstehender threading.Timer (None = keiner)
_saved_original = None     # Nutzer-Original ("" möglich); None = Slot leer
_last_written = None       # zuletzt selbst geschriebener Text
UNSAFE = object()


def clip_snapshot():
    """Keep every item and every type; refuse a format whose bytes are unreadable."""
    if not _HAS_OBJC:
        return ""
    pb = NSPasteboard.generalPasteboard()
    saved = []
    for item in pb.pasteboardItems() or []:
        formats = []
        for typ in item.types():
            data = item.dataForType_(typ)
            if data is None:
                return UNSAFE
            formats.append((typ, bytes(data)))
        saved.append(formats)
    return ("items", saved)


def clip_restore(snapshot):
    if isinstance(snapshot, tuple) and snapshot[0] == "items":
        if not _HAS_OBJC:
            return
        pb = NSPasteboard.generalPasteboard()
        items = []
        for formats in snapshot[1]:
            item = NSPasteboardItem.alloc().init()
            for typ, data in formats:
                item.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), typ)
            items.append(item)
        pb.clearContents()
        if items:
            pb.writeObjects_(items)
    elif snapshot:
        clip_copy(snapshot)
    else:
        clip_clear()


def _type_unicode(text):
    """Use Quartz Unicode events without replacing an unsafe pasteboard."""
    if not _HAS_OBJC:
        return False
    for part in (text[i:i + 32] for i in range(0, len(text), 32)):
        for down in (True, False):
            event = CGEventCreateKeyboardEvent(None, 0, down)
            CGEventSetFlags(event, 0)
            CGEventKeyboardSetUnicodeString(event, len(part), part)
            CGEventPost(kCGHIDEventTap, event)
    return True


def clip_read():
    if not _HAS_OBJC:
        return ""
    pb = NSPasteboard.generalPasteboard()
    val = pb.stringForType_(NSStringPboardType)
    return val or ""


def clip_copy(text, concealed=False):
    """Text auf das Pasteboard legen; concealed=True markiert den Eintrag
    als vertraulich (eigene Diktat-Pastes), damit Manager ihn überspringen."""
    if not _HAS_OBJC:
        return
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    if concealed:
        pb.declareTypes_owner_([NSStringPboardType, CONCEALED_TYPE], None)
        pb.setString_forType_(text, NSStringPboardType)
        pb.setString_forType_("", CONCEALED_TYPE)
    else:
        pb.setString_forType_(text, NSStringPboardType)


def clip_clear():
    if not _HAS_OBJC:
        return
    NSPasteboard.generalPasteboard().clearContents()


def _cancel_restore_locked():
    global _restore_timer
    if _restore_timer is not None:
        _restore_timer.cancel()
        _restore_timer = None


def _save_original_locked():
    """Nutzer-Original in den Slot legen, falls er leer ist. Liest nie einen
    eigenen Paste als 'Original' ein (dann gilt: kein Original bekannt)."""
    global _saved_original
    if _saved_original is not None:
        return
    current = clip_snapshot()
    if _last_written is not None and current == _last_written:
        current = ""
    _saved_original = current


def _schedule_restore_locked(delay):
    global _restore_timer
    _restore_timer = threading.Timer(delay, _restore_now)
    _restore_timer.daemon = True
    _restore_timer.start()


def _restore_now():
    """Slot-Inhalt zurückschreiben. Hat der Nutzer inzwischen selbst kopiert
    (Pasteboard != unser letzter Text), nichts überschreiben. War das
    Original leer, Pasteboard leeren statt das Diktat liegen zu lassen."""
    global _restore_timer, _saved_original, _last_written
    with _clip_lock:
        _restore_timer = None
        original = _saved_original
        _saved_original = None
        current = clip_read()
        if _last_written is not None and current != _last_written:
            _last_written = None
            return
        _last_written = None
        if original is not UNSAFE:
            clip_restore(original)


def _send_cmd_v():
    if not _HAS_OBJC:
        return
    key = _paste_keycode()
    down = CGEventCreateKeyboardEvent(None, key, True)
    CGEventSetFlags(down, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, down)
    up = CGEventCreateKeyboardEvent(None, key, False)
    CGEventSetFlags(up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, up)


def _paste_keycode():
    """Find the physical key producing V in the active Unicode keyboard layout.

    The input source is queried per paste so switching layouts while Quassel is
    running does not leave a stale shortcut. Carbon failures retain the old key.
    """
    try:
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        carbon.TISCopyCurrentKeyboardLayoutInputSource.restype = ctypes.c_void_p
        carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
        carbon.UCKeyTranslate.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
            ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint16)]
        cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        cf.CFDataGetBytePtr.restype = ctypes.c_void_p
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        prop = ctypes.c_void_p.in_dll(carbon, "kTISPropertyUnicodeKeyLayoutData")
        source = carbon.TISCopyCurrentKeyboardLayoutInputSource()
        if not source:
            return KEY_V
        try:
            data = carbon.TISGetInputSourceProperty(source, prop)
            layout = cf.CFDataGetBytePtr(data) if data else None
            if not layout:
                return KEY_V
            keyboard_type = carbon.LMGetKbdType()
            # Command may have its own mapping (for example Dvorak-QWERTY).
            # Carbon's commandKey modifier occupies bit 8 of the EventRecord;
            # UCKeyTranslate receives modifierKeyState after shifting by 8.
            for modifiers in (1, 0):
                for code in range(128):
                    dead = ctypes.c_uint32()
                    length = ctypes.c_uint32()
                    chars = (ctypes.c_uint16 * 4)()
                    status = carbon.UCKeyTranslate(layout, code, 0, modifiers,
                        keyboard_type, 1, ctypes.byref(dead), 4,
                        ctypes.byref(length), chars)
                    if status == 0 and length.value == 1 and chars[0] in (ord("v"), ord("V")):
                        return code
        finally:
            cf.CFRelease(source)
    except (AttributeError, OSError, ValueError, TypeError, ctypes.ArgumentError):
        pass
    return KEY_V


def paste(text):
    global _last_written
    with _clip_lock:
        _cancel_restore_locked()
        _save_original_locked()
        if _saved_original is UNSAFE:
            return _type_unicode(text)
        clip_copy(text, concealed=True)
        _last_written = text
        _schedule_restore_locked(RESTORE_DELAY)
    time.sleep(PASTE_SETTLE)
    _send_cmd_v()


def type_chunk(text):
    """Streaming-Häppchen einfügen, OHNE die Zwischenablage zu restaurieren
    (das macht streaming_restore() einmal am Diktatende)."""
    global _last_written
    if not text:
        return
    with _clip_lock:
        _cancel_restore_locked()
        _save_original_locked()
        if _saved_original is UNSAFE:
            return _type_unicode(text)
        clip_copy(text, concealed=True)
        _last_written = text
    time.sleep(0.12)
    _send_cmd_v()


def streaming_begin():
    """Zwischenablage vor dem Streaming in den Restore-Slot sichern."""
    with _clip_lock:
        _cancel_restore_locked()
        _save_original_locked()
        return _saved_original


def streaming_restore(old):
    """Wiederherstellung planen. Maßgeblich ist der Slot, nicht der Token —
    so stellt kein Pfad je ein eigenes Diktat 'wieder her'."""
    global _saved_original
    with _clip_lock:
        _cancel_restore_locked()
        if _saved_original is None:
            _saved_original = old or ""
        _schedule_restore_locked(STREAM_RESTORE_DELAY)


def _key_event(keycode, down):
    if not _HAS_OBJC:
        return
    ev = CGEventCreateKeyboardEvent(None, keycode, down)
    # Flags explizit nullen: sonst erben synthetische Events die gerade
    # physisch gehaltenen Modifier (Option+Backspace löscht ganze Wörter,
    # Cmd+Return schickt Mails ab).
    CGEventSetFlags(ev, 0)
    CGEventPost(kCGHIDEventTap, ev)


def send_backspaces(n):
    n = min(n, 4000)
    batch = 0
    for _ in range(n):
        _key_event(KEY_BACKSPACE, True)
        _key_event(KEY_BACKSPACE, False)
        batch += 1
        if batch >= 40:
            batch = 0
            time.sleep(0.005)


def send_enter():
    """Eingabetaste drücken (Sprachkommando 'press enter')."""
    _key_event(KEY_ENTER, True)
    _key_event(KEY_ENTER, False)


def mic_is_bluetooth(mic="default"):
    """True, wenn die aktive Aufnahmequelle ein Bluetooth-Gerät ist.

    BT-Headsets/Earbuds (AirPods etc.) schalten beim Mikrofonstart von A2DP auf
    HFP — das kostet Zeit (Anfang wird abgeschnitten) und liefert mageren Ton.
    Wird genutzt, um Vorlauf/Nachlauf großzügiger zu wählen."""
    try:
        if mic and mic != "default":
            name = mic.lower()
            return "bluetooth" in name or "airpod" in name
        return _default_input_is_bluetooth()
    except Exception:    # noqa: BLE001 — CoreAudio darf das Diktat nie stören
        return False


# CoreAudio-HAL-Zugriff über ctypes statt pyobjc: die Objective-C-Bridge für
# AudioObjectGetPropertyData (rohe C-Structs/inout-Pointer) ist in pyobjc
# nicht zuverlässig nutzbar, ctypes gegen das Framework direkt ist Standard.
class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def _fourcc(s):
    return int.from_bytes(s.encode("ascii"), "big")


_kAudioObjectSystemObject = 1
_kAudioObjectPropertyScopeGlobal = _fourcc("glob")
_kAudioObjectPropertyElementMain = 0
_kAudioHardwarePropertyDefaultInputDevice = _fourcc("dIn ")
_kAudioDevicePropertyTransportType = _fourcc("tran")
_kAudioDeviceTransportTypeBluetooth = _fourcc("blue")
_kAudioDeviceTransportTypeBluetoothLE = _fourcc("blea")


def _coreaudio_lib():
    # A fixed system framework path also works in a frozen app, where
    # find_library() may not see the host frameworks.
    try:
        return ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
    except OSError:
        return None


def _get_property_uint32(lib, object_id, selector):
    address = _AudioObjectPropertyAddress(
        selector, _kAudioObjectPropertyScopeGlobal, _kAudioObjectPropertyElementMain
    )
    value = ctypes.c_uint32(0)
    size = ctypes.c_uint32(ctypes.sizeof(value))
    err = lib.AudioObjectGetPropertyData(
        object_id, ctypes.byref(address), 0, None, ctypes.byref(size),
        ctypes.byref(value),
    )
    if err != 0:
        return None
    return value.value


def _default_input_is_bluetooth():
    lib = _coreaudio_lib()
    if lib is None:
        return False
    device_id = _get_property_uint32(
        lib, _kAudioObjectSystemObject, _kAudioHardwarePropertyDefaultInputDevice
    )
    if device_id is None:
        return False
    transport = _get_property_uint32(
        lib, device_id, _kAudioDevicePropertyTransportType
    )
    return transport in (
        _kAudioDeviceTransportTypeBluetooth,
        _kAudioDeviceTransportTypeBluetoothLE,
    )


def notify(text, ms=4000):
    """Systembenachrichtigung. ms wird auf macOS ignoriert: osascript-
    Notifications kennen keine Anzeigedauer, das Notification Center
    entscheidet selbst (Parameter bleibt für die Plattform-Schnittstelle)."""
    script = (
        f'display notification {_osa_quote(text)} '
        f'with title {_osa_quote("Quassel")}'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False,
                       timeout=OSA_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        # Hängt osascript (Notification Center klemmt), darf das den
        # aufrufenden Thread nicht mitnehmen — die Meldung entfällt dann.
        pass


def _osa_quote(s):
    """AppleScript-Stringliteral: \\ und " escapen, Zeilenumbrüche als \\n
    (rohe Newlines sind im Literal ein Syntaxfehler -> Notification ginge
    stumm verloren), übrige Steuerzeichen entfernen."""
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    s = "".join(ch for ch in s if ch >= " " or ch == "\t")
    return f'"{s}"'


# ---------------------------------------------------------- Audio-Ducking
# Backend für quassel.mediacontrol: Gesamtton stummschalten (osascript) bzw.
# spielende scriptbare Player pausieren.
class _OsaTimedOut:
    """Ersatz für das CompletedProcess, wenn osascript nicht antwortet. Leeres
    stdout heißt für die Aufrufer: Ton ist nicht stumm, es spielt nichts —
    das Ducking entfällt dann, statt das Diktat aufzuhalten."""
    stdout = ""
    stderr = ""
    returncode = -1


def _osa(script):
    try:
        return subprocess.run(["osascript", "-e", script], capture_output=True,
                              text=True, check=False, timeout=OSA_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return _OsaTimedOut()


def _output_muted():
    r = _osa("output muted of (get volume settings)")
    return r.stdout.strip() == "true"


_MUSIC_APPS = ("Music", "Spotify", "Podcasts")


def _playing_apps():
    playing = []
    for app in _MUSIC_APPS:
        script = (
            f'if application "{app}" is running then\n'
            f'  tell application "{app}" to if player state is playing then '
            f'return "playing"\n'
            f'end if\n'
            f'return "no"'
        )
        r = _osa(script)
        if r.stdout.strip() == "playing":
            playing.append(app)
    # VLC exposes a boolean `playing`, not Music's `player state` enum.
    r = _osa('if application "VLC" is running then\n'
             '  tell application "VLC" to return playing\n'
             'end if\nreturn false')
    if r.stdout.strip() == "true":
        playing.append("VLC")
    return playing


def _pause_app(app):
    _osa(f'tell application "{app}" to pause')


def _play_app(app):
    _osa(f'tell application "{app}" to play')


def duck_apply(mode):
    """'all' -> Gesamtton stumm; 'music' -> spielende Player pausieren.
    Rückgabe: Token, das duck_restore unverändert erhält."""
    if mode == "all":
        token = {"was_muted": _output_muted()}
        _osa("set volume with output muted")
        return token
    if mode == "music":
        apps = _playing_apps()
        for app in apps:
            _pause_app(app)
        return {"apps": apps}
    return None


def duck_restore(mode, token):
    if not token:
        return
    if mode == "all":
        # nur entstummen, wenn der Ton vor dem Diktat NICHT stumm war
        if not token.get("was_muted"):
            _osa("set volume without output muted")
    elif mode == "music":
        for app in token.get("apps", []):
            _play_app(app)
