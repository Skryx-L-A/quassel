"""Einstiegspunkt für die macOS-.app (von PyInstaller gebündelt).

Die App besteht aus drei Prozessen (siehe quassel/mac_app.py). Gefroren gibt
es kein "python -m quassel.daemon" mehr — die Kindprozesse sind DIESELBE exe
mit einem Subkommando als erstem Argument:

  Quassel            Haupt-App (whisper-server + Daemon + Pille + Menüleiste)
  Quassel daemon     nur der Hotkey-/Aufnahme-Daemon
  Quassel center     nur das Kontrollzentrum

Abstürze landen in ~/Library/Logs/Quassel/crash.log — die .app hat keine
Konsole, ohne Log wäre jeder Fehler unsichtbar.
"""
import os
import sys
import traceback


def _crash_log(exc):
    try:
        d = os.path.expanduser("~/Library/Logs/Quassel")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "crash.log"), "a", encoding="utf-8") as f:
            f.write("\n--- crash ---\n")
            f.write("".join(traceback.format_exception(exc)))
    except OSError:
        pass


def _run():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "daemon":
        from quassel.daemon import main
    elif cmd == "center":
        from quassel.center import main
    else:
        from quassel.mac_app import main
    main()


if __name__ == "__main__":
    try:
        _run()
    except SystemExit as e:
        if e.code not in (0, None):    # normales sys.exit(0) ist kein Absturz
            _crash_log(e)
        raise
    except BaseException as e:  # noqa: BLE001
        _crash_log(e)
        raise
