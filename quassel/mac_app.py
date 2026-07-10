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
import shutil
import signal
import subprocess
import sys

from . import server_mac
from .state import state_read

DAEMON_STOP_TIMEOUT = 5

# Finder startet Apps mit minimalem PATH (/usr/bin:/bin:...) — ohne die
# Homebrew-Pfade findet shutil.which("ffmpeg") nichts (audio.py nimmt über
# ffmpeg/AVFoundation auf). ffmpeg wird bewusst NICHT mitgebündelt.
BREW_PATHS = ["/opt/homebrew/bin", "/usr/local/bin"]
FFMPEG_HINT = ("ffmpeg fehlt — ohne ffmpeg keine Aufnahme.\n"
               "Installieren: brew install ffmpeg")


def frozen():
    """True im PyInstaller-Bundle (dann ist sys.executable die App selbst)."""
    return bool(getattr(sys, "frozen", False))


def daemon_command():
    if frozen():
        return [sys.executable, "daemon"]
    return [sys.executable, "-m", "quassel.daemon"]


def center_command():
    if frozen():
        return [sys.executable, "center"]
    return [sys.executable, "-m", "quassel.center"]


def augment_path():
    """Homebrew-Verzeichnisse an PATH anhängen (vor dem Start der Kinder,
    damit Daemon/ffmpeg sie erben). Idempotent."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    extra = [p for p in BREW_PATHS if p not in parts and os.path.isdir(p)]
    if extra:
        os.environ["PATH"] = os.pathsep.join(parts + extra)


def check_ffmpeg(tray):
    """ffmpeg vorhanden? Sonst einmalig per Tray-Notification den
    brew-Hinweis zeigen. Die App läuft weiter (Server + UI gehen auch ohne)."""
    if shutil.which("ffmpeg"):
        return True
    if tray is not None:
        tray.showMessage("Quassel", FFMPEG_HINT)
    print("mac_app: " + FFMPEG_HINT.replace("\n", " "),
          file=sys.stderr, flush=True)
    return False


class MacApp:
    """Lebenszyklus der Kindprozesse + Tray/Pille (Qt-Objekte injizierbar).

    Dient dem Kontrollzentrum zugleich als controller (wie WinApp unter
    Windows): `enabled` + `toggle()` schalten das Diktat an/aus, indem der
    Daemon-Kindprozess gestoppt/gestartet wird; Tray und Pille laufen mit."""

    def __init__(self, app):
        self.app = app
        self.daemon = None
        self.tray = None
        self.pill = None
        self.enabled = False
        self._settings = None
        self._down = False
        self._repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def start(self):
        server_mac.kill_orphans()   # Reste aus abgestürzten früheren Läufen
        server_mac.start()
        self._start_daemon()

    def _start_daemon(self):
        # Eigene Prozessgruppe: beim Shutdown wird die ganze Gruppe beendet
        # (erwischt auch Kinder des Daemons wie ffmpeg).
        self.daemon = subprocess.Popen(daemon_command(), cwd=self._repo,
                                       start_new_session=True)
        self.enabled = True

    def _stop_daemon(self):
        if self.daemon is not None:
            if self.daemon.poll() is None:
                server_mac.terminate_group(self.daemon,
                                           timeout=DAEMON_STOP_TIMEOUT)
            else:
                self.daemon.wait()      # ernten, falls von selbst gestorben
        self.daemon = None
        self.enabled = False

    def toggle(self):
        """An/Aus für das Kontrollzentrum, Tray-Menü und die Pille: Daemon
        stoppen bzw. neu starten (der Server läuft weiter — billig im
        Leerlauf, dafür sofortiges Wiedereinschalten)."""
        if self.enabled:
            self._stop_daemon()
        else:
            self._start_daemon()
        self._sync_ui()

    def _sync_ui(self):
        mode = "ready" if self.enabled else "off"
        if self.tray is not None:
            self.tray.set_mode(mode)
        if self.pill is not None:
            # pill.on unterdrückt das state.json-Polling, solange aus —
            # sonst würde die letzte Daemon-Statuszeile "off" überschreiben.
            self.pill.on = self.enabled
            self.pill.set_mode(mode)

    def open_center(self):
        """Kontrollzentrum IM Prozess öffnen (wie die Windows-Tray-App) —
        nur so bekommt es den controller und kann an/aus schalten."""
        try:
            from .center import Center
            if self._settings is None:
                self._settings = Center(controller=self)
            self._settings.show()
            self._settings.raise_()
            self._settings.activateWindow()
        except Exception:  # noqa: BLE001 — sichtbar machen statt still scheitern
            import traceback
            traceback.print_exc()

    def sync_tray(self):
        """Tray-Statuszeile aus state.json nachführen (Daemon -> UI)."""
        if self.tray is None:
            return
        if not self.enabled:
            self.tray.set_mode("off")
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
        self._stop_daemon()
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

    augment_path()
    mac = MacApp(app)
    mac.start()
    # Pille: Linksklick öffnet das Kontrollzentrum IM Prozess (mit controller),
    # Rechtsklick schaltet an/aus — wie das systemctl-Toggle unter Linux.
    pill_qt.OPEN_CENTER = mac.open_center
    pill_qt.TOGGLE = mac.toggle
    mac.tray = traymac.start_tray(app, mac.open_center, mac.quit,
                                  on_toggle=mac.toggle)
    check_ffmpeg(mac.tray)
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
