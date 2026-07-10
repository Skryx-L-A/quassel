"""macOS-Entry-Point: startet Quassel als EINE App in drei Teilen.

  * whisper-server  — Kindprozess (server_mac, Port 8765, Metal)
  * quassel.daemon  — Kindprozess (CGEventTap-Hotkey, Aufnahme, Einfügen);
                      eigener Prozess wie auf Linux (dort systemd), so bleibt
                      der Qt-Event-Loop frei und ein Daemon-Absturz reißt
                      die UI nicht mit
  * Qt (dieser Prozess) — Pille (pill_qt) + Menüleisten-Icon (traymac)

Beenden über das Menü oder SIGINT/SIGTERM räumt beide Kindprozesse auf.
"""
import os
import signal
import subprocess
import sys

from . import server_mac
from .state import state_read

DAEMON_STOP_TIMEOUT = 5


def daemon_command():
    return [sys.executable, "-m", "quassel.daemon"]


def center_command():
    return [sys.executable, "-m", "quassel.center"]


class MacApp:
    """Lebenszyklus der Kindprozesse + Tray/Pille (Qt-Objekte injizierbar)."""

    def __init__(self, app):
        self.app = app
        self.daemon = None
        self.tray = None
        self.pill = None
        self._down = False
        self._repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def start(self):
        server_mac.kill_orphans()   # Reste aus abgestürzten früheren Läufen
        server_mac.start()
        # Eigene Prozessgruppe: beim Shutdown wird die ganze Gruppe beendet
        # (erwischt auch Kinder des Daemons wie ffmpeg).
        self.daemon = subprocess.Popen(daemon_command(), cwd=self._repo,
                                       start_new_session=True)

    def open_center(self):
        subprocess.Popen(center_command(), cwd=self._repo)

    def sync_tray(self):
        """Tray-Statuszeile aus state.json nachführen (Daemon -> UI)."""
        if self.tray is None:
            return
        st = state_read()
        s = st.get("state", "idle")
        self.tray.set_mode("ready" if s == "idle" else s, st.get("text", ""))

    def shutdown(self):
        """Kindprozesse beenden — idempotent (Signal-Handler UND das Ende von
        app.exec() rufen hier hinein; nur der erste Aufruf räumt auf)."""
        if self._down:
            return
        self._down = True
        if self.daemon is not None:
            if self.daemon.poll() is None:
                server_mac.terminate_group(self.daemon,
                                           timeout=DAEMON_STOP_TIMEOUT)
            else:
                self.daemon.wait()      # ernten, falls von selbst gestorben
        self.daemon = None
        server_mac.stop()

    def quit(self):
        self.shutdown()
        self.app.quit()


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import pill_qt, traymac

    app = QApplication(sys.argv)
    app.setApplicationName("Quassel")
    app.setQuitOnLastWindowClosed(False)   # Menüleisten-App: kein Fenster nötig
    app.setWindowIcon(pill_qt.app_icon())

    # Linksklick auf die Pille öffnet das Kontrollzentrum mit unserem Python
    # (das Linux-Wrapper-Skript "quassel-type" gibt es hier nicht).
    pill_qt.CENTER_CMD = center_command()

    mac = MacApp(app)
    mac.start()
    mac.tray = traymac.start_tray(app, mac.open_center, mac.quit)
    mac.pill = pill_qt.Pill()
    mac.pill.show()

    tray_timer = QTimer()
    tray_timer.timeout.connect(mac.sync_tray)
    tray_timer.start(1000)

    # Ctrl+C/kill räumen die Kindprozesse mit auf. (Der 1-s-QTimer weckt den
    # Interpreter regelmäßig, damit Python-Signalhandler auch im Qt-Loop laufen.)
    signal.signal(signal.SIGINT, lambda *_: mac.quit())
    signal.signal(signal.SIGTERM, lambda *_: mac.quit())

    rc = app.exec()
    mac.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
