"""Plattform-Dispatch für den Daemon: wählt das OS-Backend nach sys.platform.

macOS: quassel.platform_mac. Alles andere (Linux): quassel.platform_linux.
Der Daemon läuft nicht unter Windows (dort eigener Entry-Point über
quassel/win/app.py + hook.py + machine.py) — daher kein win32-Zweig hier.

Der Import des Backends passiert erst bei tatsächlichem Attributzugriff
(PEP 562 Modul-__getattr__), nicht beim Import von quassel.platform selbst —
so bleibt z.B. der Import hier funktionsfähig, auch wenn platform_mac.py
(noch) fehlt oder auf einer anderen Plattform entwickelt wird.
"""
import importlib
import sys

_BACKEND_NAMES = ("mic_is_bluetooth", "notify", "paste", "send_backspaces",
                   "send_enter", "type_chunk", "streaming_begin", "streaming_restore")


def _backend_module_name():
    return "quassel.platform_mac" if sys.platform == "darwin" else "quassel.platform_linux"


def __getattr__(name):
    if name not in _BACKEND_NAMES:
        raise AttributeError("module 'quassel.platform' has no attribute %r" % name)
    mod = importlib.import_module(_backend_module_name())
    return getattr(mod, name)
