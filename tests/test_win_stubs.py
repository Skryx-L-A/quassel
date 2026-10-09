"""Lädt quassel/win/app.py auf macOS/Linux für Offline-Tests.

win/hook.py und win/paste.py binden beim Import ctypes.windll und sind nur
unter Windows ladbar. Für die Tests treten an ihre Stelle Attrappen, die
jeden Aufruf mitschreiben; PySide6 wird echt geladen, aber kein Fenster
erzeugt. Kein Aufruf erreicht Tastatur, Zwischenablage oder Mikrofon."""
import importlib
import sys
import types

CALLS = []


def _recorder(name, result=None):
    def fn(*args):
        CALLS.append((name,) + args)
        return result
    fn.__name__ = name
    return fn


def _paste_module():
    mod = types.ModuleType("quassel.win.paste")
    for name in ("paste", "clip_copy", "clip_read", "send_backspaces", "send_enter", "type_chunk",
                 "streaming_restore"):
        setattr(mod, name, _recorder(name))
    mod.streaming_begin = _recorder("streaming_begin", "clip-backup")
    return mod


def _hook_module():
    mod = types.ModuleType("quassel.win.hook")
    mod.CHORDS_VK = {"ctrl+meta": ({0xA2, 0xA3}, {0x5B, 0x5C}),
                     "ctrl+alt": ({0xA2, 0xA3}, {0xA4, 0xA5})}

    class KeyboardHook:
        def __init__(self):
            import queue
            self.events = queue.Queue()

        def start(self):
            pass

        def stop(self):
            pass
    mod.KeyboardHook = KeyboardHook
    return mod


def load_win_app(monkeypatch):
    """quassel.win.app frisch importieren; die Attrappen gelten nur für die
    Dauer des Tests (monkeypatch räumt sys.modules wieder auf)."""
    CALLS.clear()
    monkeypatch.setitem(sys.modules, "quassel.win.paste", _paste_module())
    monkeypatch.setitem(sys.modules, "quassel.win.hook", _hook_module())
    # setitem merkt sich den Vorzustand (meist: fehlt) und stellt ihn am
    # Testende wieder her; der Platzhalter selbst wird sofort entfernt, damit
    # der Import unten die Attrappen sieht.
    monkeypatch.setitem(sys.modules, "quassel.win.app", None)
    del sys.modules["quassel.win.app"]
    import quassel.win as win_pkg
    # Paketattribute merken bzw. nach dem Test wieder entfernen: sonst
    # sähe ein späterer Test die Attrappen über quassel.win.paste.
    for name in ("app", "paste", "hook"):
        monkeypatch.setattr(win_pkg, name, None, raising=False)
    return importlib.import_module("quassel.win.app")


def test_the_stubs_do_not_outlive_the_test(monkeypatch):
    """Die Attrappen gelten nur innerhalb eines Tests."""
    from _pytest.monkeypatch import MonkeyPatch
    inner = MonkeyPatch()
    app = load_win_app(inner)
    assert sys.modules["quassel.win.paste"].paste.__name__ == "paste"
    assert app.WinDictation is not None
    inner.undo()
    assert "quassel.win.app" not in sys.modules
    assert "quassel.win.paste" not in sys.modules
