"""Einfügen unter Windows: Zwischenablage (Win32) + SendInput Strg+V.

Wie auf Linux layout-sicher über die Zwischenablage; die vorherige
Text-Zwischenablage wird nach 2 s wiederhergestellt. Backspaces für das
Sprachkommando „lösch das" gehen ebenfalls über SendInput.
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

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

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
VK_CONTROL, VK_V, VK_BACK = 0x11, 0x56, 0x08


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
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        return False
    try:
        user32.EmptyClipboard()
        # Ab SetClipboardData gehört das Handle dem System
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
    finally:
        user32.CloseClipboard()
    return True


def paste(text):
    old = clip_read()
    clip_copy(text)
    time.sleep(0.15)
    _send_keys([(VK_CONTROL, True), (VK_V, True), (VK_V, False),
                (VK_CONTROL, False)])
    if old:
        def restore():
            time.sleep(2)
            clip_copy(old)
        threading.Thread(target=restore, daemon=True).start()


def type_chunk(text):
    """Streaming-Häppchen einfügen, OHNE die Zwischenablage zu restaurieren
    (streaming_restore() macht das einmal am Diktatende)."""
    if not text:
        return
    clip_copy(text)
    time.sleep(0.08)
    _send_keys([(VK_CONTROL, True), (VK_V, True), (VK_V, False),
                (VK_CONTROL, False)])


def streaming_begin():
    return clip_read()


def streaming_restore(old):
    if old:
        def restore():
            time.sleep(1.5)
            clip_copy(old)
        threading.Thread(target=restore, daemon=True).start()


def send_backspaces(n):
    n = min(n, 4000)
    steps = []
    for _ in range(n):
        steps += [(VK_BACK, True), (VK_BACK, False)]
    while steps:
        _send_keys(steps[:200])
        steps = steps[200:]
