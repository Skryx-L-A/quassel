"""Low-Level-Tastatur-Hook für Windows (ctypes, WH_KEYBOARD_LL).

Erkennt den Modifier-Chord (Standard Strg+Win) mit derselben Semantik wie
der Linux-evdev-Daemon: Halten = Push-to-Talk, Doppeltipp = Freihand,
andere Taste während des Chords = Abbruch. Die Events landen in einer
Queue; die Zustandsmaschine (machine.py-Logik in app.py) läuft im
Qt-Hauptthread.

Eigene injizierte Events (SendInput beim Einfügen) werden über das
LLKHF_INJECTED-Flag ignoriert.
"""
import ctypes
import ctypes.wintypes as wt
import queue
import threading

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
LLKHF_INJECTED = 0x10

# Virtual-Key-Codes der Modifier (links/rechts getrennt, wie evdev)
VK_CTRL = {0xA2, 0xA3}
VK_META = {0x5B, 0x5C}      # Windows-Tasten
VK_ALT = {0xA4, 0xA5}
CHORDS_VK = {
    "ctrl+meta": (VK_CTRL, VK_META),
    "alt+meta": (VK_ALT, VK_META),
    "ctrl+alt": (VK_CTRL, VK_ALT),
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int,
                              wt.WPARAM, wt.LPARAM)


class KeyboardHook(threading.Thread):
    """Liefert (vk, pressed)-Tupel über self.events (queue.Queue)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.events = queue.Queue()
        self._proc = HOOKPROC(self._callback)   # Referenz halten (GC!)
        self._hook = None
        self._tid = None

    def _callback(self, n_code, w_param, l_param):
        if n_code == 0:
            kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not kb.flags & LLKHF_INJECTED:
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    self.events.put((kb.vkCode, True))
                elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                    self.events.put((kb.vkCode, False))
        return ctypes.windll.user32.CallNextHookEx(None, n_code, w_param, l_param)

    def run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)

    def stop(self):
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
