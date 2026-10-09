"""Einfügen unter Windows: Zwischenablage (Win32) + SendInput Strg+V.

Wie auf Linux layout-sicher über die Zwischenablage; die vorherige
Text-Zwischenablage wird nach 2 s wiederhergestellt. Backspaces für das
Sprachkommando „lösch das" gehen ebenfalls über SendInput.
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

from .. import clipwait

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 64-bit-sichere Signaturen: ohne restype kürzt ctypes Handles/Zeiger auf
# c_int — GlobalLock auf dem verstümmelten Handle liefert dann NULL/Müll
# und das Einfügen stirbt mit einer Access-Violation.
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.GetClipboardData.restype = wt.HANDLE
user32.GetClipboardData.argtypes = [wt.UINT]
user32.SetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
user32.RegisterClipboardFormatW.restype = wt.UINT
user32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
user32.EnumClipboardFormats.restype = wt.UINT
user32.EnumClipboardFormats.argtypes = [wt.UINT]

CF_UNICODETEXT = 13
CF_TEXT, CF_OEMTEXT, CF_DIB, CF_HDROP, CF_LOCALE, CF_DIBV5 = 1, 7, 8, 15, 16, 17
SAFE_FORMATS = {CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_DIB, CF_HDROP, CF_LOCALE, CF_DIBV5}
PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing",
                   "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
VK_CONTROL, VK_V, VK_BACK, VK_RETURN = 0x11, 0x56, 0x08, 0x0D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", _INPUTUNION)]


def _send_keys(steps):
    """steps: Liste (vk, pressed)."""
    inputs = (INPUT * len(steps))()
    for i, (vk, pressed) in enumerate(steps):
        inputs[i].type = INPUT_KEYBOARD
        inputs[i].union.ki = KEYBDINPUT(
            vk, 0, 0 if pressed else KEYEVENTF_KEYUP, 0, None)
    user32.SendInput(len(steps), inputs, ctypes.sizeof(INPUT))


def _type_unicode(text):
    """Fallback without touching clipboard; SendInput accepts UTF-16 units."""
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        code = int.from_bytes(raw[i:i + 2], "little")
        inputs = (INPUT * 2)()
        for j, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
            inputs[j].type = INPUT_KEYBOARD
            inputs[j].union.ki = KEYBDINPUT(0, code, flags, 0, None)
        user32.SendInput(2, inputs, ctypes.sizeof(INPUT))


def _format_ids():
    return {name: user32.RegisterClipboardFormatW(name) for name in
            (*PRIVATE_FORMATS, "HTML Format", "Rich Text Format")}


def _snapshot():
    """Return full supported HGLOBAL contents, or None for an unsafe format."""
    if not user32.OpenClipboard(None):
        return None
    try:
        names = _format_ids()
        safe = SAFE_FORMATS | {names["HTML Format"], names["Rich Text Format"]}
        private = {names[name] for name in PRIVATE_FORMATS}
        saved = []
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in private:
                continue
            if fmt not in safe:
                return None
            handle = user32.GetClipboardData(fmt)
            if not handle:
                return None
            size = kernel32.GlobalSize(handle)
            ptr = kernel32.GlobalLock(handle)
            if not ptr or not size:
                return None
            try:
                saved.append((fmt, ctypes.string_at(ptr, size)))
            finally:
                kernel32.GlobalUnlock(handle)
        return saved
    finally:
        user32.CloseClipboard()


def _put_bytes(fmt, data):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def _write_snapshot(saved, private=False):
    if not user32.OpenClipboard(None):
        return False
    try:
        if not user32.EmptyClipboard():
            return False
        for fmt, data in saved:
            if not _put_bytes(fmt, data):
                return False
        if private:
            names = _format_ids()
            for name in PRIVATE_FORMATS:
                value = b"\x00\x00\x00\x00"
                if not _put_bytes(names[name], value):
                    return False
        return True
    finally:
        user32.CloseClipboard()


def clip_read():
    text = ""
    if not user32.OpenClipboard(None):
        return text
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if handle:
            ptr = kernel32.GlobalLock(handle)
            if ptr:
                text = ctypes.wstring_at(ptr)
                kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
    return text


def clip_copy(text):
    return _write_snapshot([(CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")],
                           private=True)


_slot_lock = threading.Lock()
_original = None
_last_written = None
_generation = 0


def _begin():
    global _original, _last_written
    with _slot_lock:
        if _original is not None and _last_written is not None and clip_read() != _last_written:
            _original = None
            _last_written = None
        if _original is None:
            _original = _snapshot()
        return _original


def _restore_if_ours(delay, generation):
    global _original, _last_written
    time.sleep(delay)
    with _slot_lock:
        if generation != _generation:
            return
        saved, written = _original, _last_written
        _original = None
        _last_written = None
        if saved is not None and written is not None and clip_read() == written:
            _write_snapshot(saved)


# Obergrenze der Wartezeit vor Strg+V: gewartet wird nur, bis die
# Zwischenablage den Text zurückgibt; die festen Werte waren früher die Pause.
PASTE_SETTLE = 0.15
CHUNK_SETTLE = 0.08


def paste(text, extra_delay=0.0):
    """extra_delay: zusätzliche Pause vor Strg+V aus dem Per-App-Profil."""
    global _last_written, _generation
    if _begin() is None:
        _type_unicode(text)
        return True
    if not clip_copy(text):
        return False
    with _slot_lock:
        _last_written = text
        _generation += 1
        generation = _generation
    clipwait.wait_until_holds(clip_read, text, PASTE_SETTLE)
    if extra_delay > 0:
        time.sleep(extra_delay)
    _send_keys([(VK_CONTROL, True), (VK_V, True), (VK_V, False),
                (VK_CONTROL, False)])
    threading.Thread(target=_restore_if_ours, args=(2, generation), daemon=True).start()
    return True


def type_chunk(text):
    """Streaming-Häppchen einfügen, OHNE die Zwischenablage zu restaurieren
    (streaming_restore() macht das einmal am Diktatende)."""
    if not text:
        return
    global _last_written, _generation
    if _begin() is None:
        _type_unicode(text)
        return True
    if not clip_copy(text):
        return False
    with _slot_lock:
        _last_written = text
        _generation += 1
    clipwait.wait_until_holds(clip_read, text, CHUNK_SETTLE)
    _send_keys([(VK_CONTROL, True), (VK_V, True), (VK_V, False),
                (VK_CONTROL, False)])


def streaming_begin():
    return _begin()


def streaming_restore(old):
    if old is not None:
        with _slot_lock:
            generation = _generation
        threading.Thread(target=_restore_if_ours, args=(1.5, generation), daemon=True).start()


def send_backspaces(n):
    n = min(n, 4000)
    steps = []
    for _ in range(n):
        steps += [(VK_BACK, True), (VK_BACK, False)]
    while steps:
        _send_keys(steps[:200])
        steps = steps[200:]


def send_enter():
    """Eingabetaste drücken (Sprachkommando 'press enter')."""
    _send_keys([(VK_RETURN, True), (VK_RETURN, False)])
