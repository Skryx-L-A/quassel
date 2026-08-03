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
import time

from . import audio, server_mac
from .state import state_read

DAEMON_STOP_TIMEOUT = 5

# Aufsicht über den Daemon-Kindprozess. Er beendet sich bei verklemmtem
# CoreAudio selbst (Exit-Code 3) und wird dann neu gestartet; die Begrenzung
# verhindert Dauerfeuer, wenn der Neustart nichts hilft.
SUPERVISE_EVERY = 2000     # ms
RESTART_WINDOW = 300.0     # s
RESTART_LIMIT = 5
# Muss zu quassel/daemon.py::RESTART_EXIT passen (ein Test hält beides zusammen).
# Nur dieser Code bedeutet „bitte neu starten"; wer den Daemon von außen beendet,
# bekommt ihn nicht ungefragt zurück.
DAEMON_RESTART_EXIT = 3
RESTART_GIVEUP_HINT = ("Diktat-Dienst startet wiederholt neu, bitte Log prüfen: "
                       "~/Library/Logs/Quassel/daemon.log")

# Finder startet Apps mit minimalem PATH (/usr/bin:/bin:...) — ohne die
# Homebrew-Pfade findet shutil.which("ffmpeg") nichts. Die Aufnahme braucht
# ffmpeg seit dem sounddevice-Backend nicht mehr; die Datei-Transkription
# von Nicht-WAV-Formaten schon. ffmpeg wird bewusst NICHT mitgebündelt.
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
    brew-Hinweis zeigen. Die App läuft weiter (Server + UI gehen auch ohne).

    Nur relevant, wenn das ffmpeg-Aufnahme-Backend aktiv ist — der Default
    nimmt über sounddevice auf und braucht ffmpeg nicht."""
    if audio.mac_backend() != "ffmpeg":
        return True
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
        self._restarts = []          # monotone Zeitpunkte der letzten Neustarts
        self._repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def start(self):
        server_mac.kill_orphans()   # Reste aus abgestürzten früheren Läufen
        server_mac.start()
        self._start_daemon()

    def _start_daemon(self):
        # Eigene Prozessgruppe: beim Shutdown wird die ganze Gruppe beendet
        # (erwischt auch Kinder des Daemons wie ffmpeg).
        # stderr/stdout in Logdatei: bei Start über Finder/open ginge die
        # Ausgabe (u.a. fehlende TCC-Freigaben) sonst verloren.
        log_dir = os.path.expanduser("~/Library/Logs/Quassel")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "daemon.log"), "ab") as log_f:
            self.daemon = subprocess.Popen(daemon_command(), cwd=self._repo,
                                           start_new_session=True,
                                           stdout=log_f, stderr=log_f)
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

    def _supervise(self, now=None):
        """Läuft der Daemon noch? Er beendet sich bei verklemmtem Audiogerät
        selbst und stirbt auch sonst still — ohne Aufsicht wäre das Diktat
        danach einfach weg, ohne dass es jemand merkt.

        Nach einem bewussten Ausschalten (enabled False) wird nichts neu
        gestartet."""
        if not self.enabled or self.daemon is None:
            return
        if self.daemon.poll() is None:
            return
        code = self.daemon.returncode
        self.daemon.wait()           # Zombie einsammeln (Prozess ist schon weg)
        self.daemon = None
        if code != DAEMON_RESTART_EXIT:
            print("mac_app: Daemon beendet (code=%s) -> kein Neustart" % code,
                  file=sys.stderr, flush=True)
            self.enabled = False
            self._sync_ui()
            return
        now = now if now is not None else time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < RESTART_WINDOW]
        if len(self._restarts) >= RESTART_LIMIT:
            print("mac_app: Daemon beendet (code=%s), zu viele Neustarts -> aus"
                  % code, file=sys.stderr, flush=True)
            self.enabled = False
            self._sync_ui()
            if self.tray is not None:
                self.tray.showMessage("Quassel", RESTART_GIVEUP_HINT)
            return
        self._restarts.append(now)
        print("mac_app: Daemon beendet (code=%s) -> Neustart (%d in %d Minuten)"
              % (code, len(self._restarts), int(RESTART_WINDOW // 60)),
              file=sys.stderr, flush=True)
        self._start_daemon()
        self._sync_ui()

    def panic(self):
        """Not-Aus aus dem Menüleisten-Menü: SIGUSR2 an den Daemon-Prozess —
        er beendet eine laufende Aufnahme sofort, auch wenn sein Hotkey
        hängt. Läuft kein Daemon, gibt es nichts zu beenden."""
        if self.daemon is None or self.daemon.poll() is not None:
            return
        try:
            os.kill(self.daemon.pid, signal.SIGUSR2)
        except (OSError, AttributeError):
            pass

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
                                  on_toggle=mac.toggle, on_panic=mac.panic)
    check_ffmpeg(mac.tray)
    mac.pill = pill_qt.Pill()
    mac.pill.show()

    tray_timer = QTimer()
    tray_timer.timeout.connect(mac.sync_tray)
    tray_timer.start(1000)

    # Aufsicht: startet den Daemon neu, wenn er sich beendet hat
    watch_timer = QTimer()
    watch_timer.timeout.connect(mac._supervise)
    watch_timer.start(SUPERVISE_EVERY)

    # Ctrl+C/kill räumen die Kindprozesse mit auf. (Der 1-s-QTimer weckt den
    # Interpreter regelmäßig, damit Python-Signalhandler auch im Qt-Loop laufen.)
    signal.signal(signal.SIGINT, lambda *_: mac.quit())
    signal.signal(signal.SIGTERM, lambda *_: mac.quit())

    rc = app.exec()
    mac.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
