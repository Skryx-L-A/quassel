"""VoxType für Windows — alles in einem Qt-Prozess.

Tray-Icon (an/aus, Einstellungen, Beenden) + schwebende Pille + Hotkey-Hook
+ lokaler whisper-server.exe. Teilt config/i18n/textproc/whisperclient mit
der Linux-Version; nur Tastatur/Audio/Einfügen sind Windows-spezifisch.
"""
import os
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .. import __version__, config, i18n, textproc, whisperclient
from ..audio import RATE, SAMPLE_BYTES, wav_from_raw
from ..config import CHORDS
from ..i18n import tr
from ..state import PARTWAV, WAV, state_set
from . import server
from .audio_win import Recorder
from .hook import CHORDS_VK, KeyboardHook
from .machine import ChordMachine
from .paste import paste, send_backspaces

PARTIAL_EVERY = 2.0
PARTIAL_WINDOW = 15
ICON_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "..", "..", "assets", "voxtype.svg"),
    os.path.join(os.path.dirname(__file__), "..", "..", "assets", "voxtype.png"),
]


def app_icon():
    for p in ICON_CANDIDATES:
        if os.path.exists(p):
            return QIcon(p)
    # Notfall: einfarbiges Icon zeichnen
    from PySide6.QtGui import QPixmap
    pix = QPixmap(64, 64)
    pix.fill(QColor("#6A2EE0"))
    return QIcon(pix)


class PartialWorker(threading.Thread):
    """Live-Vorschau: Teiltranskripte während der Aufnahme."""

    def __init__(self, rec, cfg):
        super().__init__(daemon=True)
        self.rec, self.cfg = rec, cfg
        self.stop_event = threading.Event()

    def run(self):
        whisperclient.ensure_server()
        while not self.stop_event.wait(PARTIAL_EVERY):
            if not self.rec.active:
                return
            data = self.rec.raw_bytes()
            if len(data) < RATE * SAMPLE_BYTES // 2:
                continue
            data = data[-(RATE * SAMPLE_BYTES * PARTIAL_WINDOW):]
            try:
                wav_from_raw(data, PARTWAV)
            except OSError:
                continue
            text = whisperclient.transcribe(PARTWAV, self.cfg, timeout=20)
            if text is not None and not self.stop_event.is_set() and self.rec.active:
                state_set("recording", " ".join(text.split()).strip())

    def stop(self):
        self.stop_event.set()


class Pill(QWidget):
    """Schwebende Pille unten-mittig auf dem aktuellen Monitor."""

    def __init__(self):
        super().__init__(None,
                         Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.cfg = config.Cfg()
        self.mode = "ready"
        self.t0 = time.monotonic()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 8)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("color: #e8e8ee; font-size: 12px;")
        lay.addWidget(self.label)
        self.set_mode("ready")
        anim = QTimer(self)
        anim.timeout.connect(self.update)
        anim.start(80)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        op = max(0.15, min(1.0, self.cfg.pill_opacity))
        p.setBrush(QColor(16, 16, 22, int(op * 255)))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 16, 16)
        if self.mode == "recording":
            breathe = 0.5 + 0.5 * __import__("math").sin(
                (time.monotonic() - self.t0) * 2 * 3.14159 / 2.4)
            r = 5 + 1.5 * breathe
            p.setBrush(QColor(255, 84, 84, int((0.65 + 0.35 * breathe) * 255)))
            p.drawEllipse(self.rect().center().x() - int(r), 12 - int(r) + 2,
                          int(r * 2), int(r * 2))

    def set_mode(self, mode, text=""):
        self.mode = mode
        if mode == "recording":
            self.t0 = time.monotonic()
            self.label.setText("   " + (text or tr("recording")))
        elif mode == "transcribing":
            self.label.setText(tr("transcribing"))
        elif mode in ("done", "error"):
            self.label.setText(text)
        else:
            self.label.setText(tr("ready"))
        self.adjustSize()
        self.reposition()
        self.setVisible(self.cfg.pill_enabled)

    def reposition(self):
        """Auf den Monitor mit dem Mauszeiger (= aktiver Bildschirm)."""
        screen = QGuiApplication.screenAt(QGuiApplication.primaryScreen().geometry().center())
        cursor = self.cursor().pos()
        s = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        geo = s.availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.bottom() - self.height() - 40)


class WinApp(QObject):
    sig_state = Signal(str, str)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = config.Cfg()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        whisperclient.STARTER = server.start          # Server-Start = Windows-Variante
        self.rec = Recorder()
        self.partial = None
        self.last_paste_len = 0
        self.enabled = True

        self.pill = Pill()
        self.sig_state.connect(self._apply_state)

        ga, gb = CHORDS_VK[self.cfg.chord]
        self.machine = ChordMachine(
            ga, gb, self.on_start, self.on_finish, self.on_cancel,
            self.cfg.hold_min, self.cfg.double_window)
        self.hook = KeyboardHook()
        self.hook.start()

        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip("VoxType")
        self.build_menu()
        self.tray.show()

        self.poll = QTimer()
        self.poll.timeout.connect(self.pump)
        self.poll.start(20)

        if not server.installed():
            threading.Thread(target=self.first_run_setup, daemon=True).start()

    # ----------------------------------------------------------- Ersteinrichtung
    def first_run_setup(self):
        self.sig_state.emit("transcribing", tr("downloading", model="…"))
        try:
            if server.server_exe() is None:
                server.download_binaries()
            if server.current_model() is None:
                model = "small" if not server.has_nvidia() else "large-v3-turbo"
                server.download_model(model)
            self.sig_state.emit("done", tr("ready"))
        except Exception as e:  # noqa: BLE001
            self.sig_state.emit("error", str(e)[:80])

    # ------------------------------------------------------------------ Menü
    def build_menu(self):
        menu = QMenu()
        self.act_toggle = QAction(tr("turn_off"), menu)
        self.act_toggle.triggered.connect(self.toggle)
        menu.addAction(self.act_toggle)
        act_settings = QAction(tr("nav_general"), menu)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)
        menu.addSeparator()
        act_quit = QAction(tr("turn_off") + " / Quit", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)

    def open_settings(self):
        # Das Qt-Kontrollzentrum (center.py) ist plattformneutral nutzbar.
        from ..center import Center
        if not hasattr(self, "_settings") or self._settings is None:
            self._settings = Center()
        self._settings.show()
        self._settings.raise_()

    def toggle(self):
        self.enabled = not self.enabled
        self.act_toggle.setText(tr("turn_off") if self.enabled else tr("turn_on"))
        if not self.enabled and self.rec.active:
            self.on_cancel("canceled_key")
        self.sig_state.emit("ready" if self.enabled else "off", "")

    def quit(self):
        self.hook.stop()
        server.stop()
        self.app.quit()

    # --------------------------------------------------------------- Hotkey
    def pump(self):
        while not self.hook.events.empty():
            vk, pressed = self.hook.events.get()
            if self.enabled:
                self.machine.key(vk, pressed)
        self.machine.poll()

    def on_start(self):
        self.cfg.reload()
        if not self.rec.start(self.cfg.mic):
            return
        self.partial = PartialWorker(self.rec, self.cfg)
        self.partial.start()
        self.sig_state.emit("recording", "")

    def on_cancel(self, reason_key):
        if self.partial:
            self.partial.stop()
            self.partial = None
        self.rec.stop()
        self.sig_state.emit("ready", "")

    def on_finish(self):
        if self.partial:
            self.partial.stop()
            self.partial = None
        self.rec.stop()
        data = self.rec.raw_bytes()
        if len(data) < 8000:
            self.sig_state.emit("ready", "")
            return
        self.sig_state.emit("transcribing", "")
        threading.Thread(target=self._transcribe, args=(data,), daemon=True).start()

    def _transcribe(self, data):
        if not whisperclient.ensure_server():
            self.sig_state.emit("error", tr("no_server"))
            return
        wav_from_raw(data, WAV)
        raw = whisperclient.transcribe(WAV, self.cfg)
        if raw is None:
            self.sig_state.emit("error", tr("no_server"))
            return
        kind, value = textproc.postprocess(raw, self.cfg)
        if kind is None:
            self.sig_state.emit("error", tr("nothing"))
            return
        if kind == "command":
            if self.last_paste_len:
                send_backspaces(self.last_paste_len)
                self.last_paste_len = 0
                self.sig_state.emit("done", tr("deleted"))
            else:
                self.sig_state.emit("error", tr("nothing"))
            return
        paste(value)
        self.last_paste_len = len(value)
        if self.cfg.history_enabled:
            try:
                config.history_append(value)
            except OSError:
                pass
        self.sig_state.emit("done", value)

    def _apply_state(self, state, text):
        self.pill.cfg.reload()
        self.pill.set_mode(state, text)
        if state in ("done", "error"):
            QTimer.singleShot(3000, lambda: self.pill.set_mode(
                "ready" if self.enabled else "off"))


def main():
    if os.name != "nt":
        raise SystemExit("voxtype.win.app läuft nur unter Windows.")
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("VoxType")
    app.setWindowIcon(app_icon())
    WinApp(app)
    app.exec()


if __name__ == "__main__":
    main()
