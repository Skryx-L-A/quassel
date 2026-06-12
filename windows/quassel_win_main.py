"""Einstiegspunkt für die Windows-exe (von PyInstaller gebündelt).

Abstürze landen in %LOCALAPPDATA%\\Quassel\\crash.log — die Fenster-exe hat
keine Konsole, ohne Log wäre jeder Fehler nur ein anonymer Dialog.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _crash_log(exc):
    try:
        d = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Quassel")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "crash.log"), "a", encoding="utf-8") as f:
            f.write("\n--- crash ---\n")
            f.write("".join(traceback.format_exception(exc)))
    except OSError:
        pass


if __name__ == "__main__":
    try:
        from quassel.win.app import main
        main()
    except BaseException as e:  # noqa: BLE001
        _crash_log(e)
        raise
