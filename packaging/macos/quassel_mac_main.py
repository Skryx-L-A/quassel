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


def _handle_server_restart(app):
    """Honor a fresh request only from this App's live daemon child."""
    import json
    import time
    from quassel import fsutil, profile, server_mac
    from quassel.state import RUNDIR

    request_path = os.path.join(RUNDIR, "server-restart.request")
    ack_path = os.path.join(RUNDIR, "server-restart.ack")
    try:
        with open(request_path, encoding="utf-8") as source:
            request = json.load(source)
    except (OSError, ValueError):
        return
    try:
        os.remove(request_path)
    except OSError:
        pass
    if not isinstance(request, dict):
        return
    child = app.daemon
    if (not app.enabled or child is None or child.poll() is not None
            or request.get("daemon_pid") != child.pid
            or request.get("app_pid") != os.getpid()
            or request.get("profile") != profile.name()
            or not isinstance(request.get("token"), str)
            or not isinstance(request.get("created"), (int, float))
            or abs(time.time() - request["created"]) > 30):
        return
    owned = server_mac._proc is not None and server_mac._proc.poll() is None
    started = False
    if owned:
        try:
            server_mac.stop()
            started = bool(server_mac.start())
            started = (started and server_mac._proc is not None
                       and server_mac._proc.poll() is None)
        except (OSError, ValueError):
            started = False
    fsutil.atomic_write(ack_path, json.dumps({"token": request["token"],
                                              "started": started}), mode=0o600)


def _install_server_restart_handler():
    from quassel.mac_app import MacApp
    original = MacApp._supervise

    def supervise(app, now=None):
        _handle_server_restart(app)
        return original(app, now)

    MacApp._supervise = supervise


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
        _install_server_restart_handler()
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
