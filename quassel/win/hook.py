"""Tastatur-Mitleser für Windows über Raw Input (WM_INPUT).

Vorher WH_KEYBOARD_LL: dessen Callback läuft synchron in der systemweiten
Eingabekette. Der Callback ist Python-Code und braucht den GIL — hält ein
anderer Thread den GIL zu lange (Transkription, Hänger), staut Windows
ALLE Tastatureingaben systemweit. Thread-Priorität ändert daran nichts.

Raw Input (RIDEV_INPUTSINK) ist dagegen ein passiver Abonnent: Windows
kopiert die Events in die Message-Queue dieses Threads, die Eingabekette
läuft unabhängig davon weiter. Selbst wenn Quassel komplett hängt, bleibt
die Systemtastatur flüssig. Tasten unterdrücken konnten wir mit dem Hook
ohnehin nie nötig — der Chord (Strg+Win) löst allein nichts aus.

Erkennt den Modifier-Chord mit derselben Semantik wie der Linux-evdev-
Daemon: Halten = Push-to-Talk, Doppeltipp = Freihand, andere Taste während
des Chords = Abbruch. Die Events landen in einer Queue; die Zustands-
maschine (machine.py) läuft im Qt-Hauptthread.

Eigene injizierte Events (SendInput beim Einfügen) kommen ohne Geräte-
Handle an (hDevice == NULL) und werden ignoriert.
"""
import ctypes
import ctypes.wintypes as wt
import queue
import threading

WM_INPUT = 0x00FF
WM_QUIT = 0x0012
HWND_MESSAGE = -3
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEKEYBOARD = 1
RI_KEY_BREAK = 0x01
RI_KEY_E0 = 0x02

# Virtual-Key-Codes der Modifier (links/rechts getrennt, wie evdev)
VK_CTRL = {0xA2, 0xA3}
VK_META = {0x5B, 0x5C}      # Windows-Tasten
VK_ALT = {0xA4, 0xA5}
CHORDS_VK = {
    "ctrl+meta": (VK_CTRL, VK_META),
    "alt+meta": (VK_ALT, VK_META),
    "ctrl+alt": (VK_CTRL, VK_ALT),
}


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD),
                ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wt.USHORT), ("Flags", wt.USHORT),
                ("Reserved", wt.USHORT), ("VKey", wt.USHORT),
                ("Message", wt.UINT), ("ExtraInformation", wt.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD)]


class KeyboardHook(threading.Thread):
    """Liefert (vk, pressed)-Tupel über self.events (queue.Queue)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.events = queue.Queue()
        self._tid = None

    def run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        # 64-bit-sichere Signaturen (ctypes kürzt sonst Handles auf c_int)
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        user32.GetRawInputData.restype = wt.UINT
        user32.GetRawInputData.argtypes = [
            wt.LPARAM, wt.UINT, wt.LPVOID, ctypes.POINTER(wt.UINT), wt.UINT]
        # Unsichtbares Message-Only-Fenster als Empfänger der WM_INPUT-Events
        hwnd = user32.CreateWindowExW(0, "STATIC", "quassel-rawinput", 0,
                                      0, 0, 0, 0, HWND_MESSAGE, None, None, None)
        rid = RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd)  # Tastaturen
        user32.RegisterRawInputDevices(ctypes.byref(rid), 1,
                                       ctypes.sizeof(RAWINPUTDEVICE))
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_INPUT:
                self._on_input(msg.lParam)
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if hwnd:
            user32.DestroyWindow(hwnd)

    def _on_input(self, lparam):
        raw = RAWINPUT()
        size = wt.UINT(ctypes.sizeof(RAWINPUT))
        got = ctypes.windll.user32.GetRawInputData(
            lparam, RID_INPUT, ctypes.byref(raw), ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER))
        if got == 0 or got == 0xFFFFFFFF:
            return
        if raw.header.dwType != RIM_TYPEKEYBOARD:
            return
        if not raw.header.hDevice:
            return                          # injiziert (SendInput) -> ignorieren
        kb = raw.keyboard
        vk = kb.VKey
        if vk == 0xFF:
            return                          # Füll-Events (z.B. Fake-Shift)
        # Raw Input meldet Strg/Alt generisch; links/rechts steckt im E0-Flag
        if vk == 0x11:                      # VK_CONTROL
            vk = 0xA3 if kb.Flags & RI_KEY_E0 else 0xA2
        elif vk == 0x12:                    # VK_MENU
            vk = 0xA5 if kb.Flags & RI_KEY_E0 else 0xA4
        elif vk == 0x10:                    # VK_SHIFT
            vk = 0xA1 if kb.MakeCode == 0x36 else 0xA0
        self.events.put_nowait((vk, not (kb.Flags & RI_KEY_BREAK)))

    def stop(self):
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
