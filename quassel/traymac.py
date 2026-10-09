"""macOS menu bar icon via QSystemTrayIcon.

Status line (non-clickable): Ready/Recording/…
Menu items: Turn off, Stop dictation now, Raw text back, Open Control Center,
Quit Quassel
(English or German, following the app language; retranslate() after a switch).
Icon: monochromatic template-style for native macOS menu bar rendering.
"""
import ctypes
import os
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from .i18n import tr

STATUS_KEYS = {
    "ready": "tray_ready",
    "recording": "tray_recording",
    "transcribing": "tray_transcribing",
    "done": "tray_done",
    "error": "tray_error",
    "off": "tray_off",
}


CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"
_secure_fn = []


def secure_input_enabled():
    """Ist irgendwo „Sichere Eingabe" an (Passwortfeld, Terminal mit
    „Sichere Tastatureingabe")? Dann liefert macOS keinem Event-Tap mehr
    Tasten, der Diktier-Hotkey kommt nicht an (H120). Reine Abfrage über
    Carbon/HIToolbox, systemweit gültig; False, wenn nicht ermittelbar."""
    if sys.platform != "darwin":
        return False
    try:
        if not _secure_fn:
            fn = ctypes.cdll.LoadLibrary(CARBON).IsSecureEventInputEnabled
            fn.restype = ctypes.c_bool
            fn.argtypes = []
            _secure_fn.append(fn)
        return bool(_secure_fn[0]())
    except (OSError, AttributeError):
        return False


def _create_mic_icon(size=22, dpr=2.0):
    """Draw a simple monochromatic mic glyph for macOS menu bar.

    macOS template icons are rendered in black on light backgrounds,
    white on dark. We draw in black; Qt handles the inversion.
    Drawn at dpr x resolution with devicePixelRatio set, so the glyph
    stays sharp on Retina displays.
    """
    size = int(size * dpr)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.black)
    painter.setBrush(Qt.black)

    # Mic capsule: rounded rect at top
    capsule_w = size * 0.4
    capsule_h = size * 0.5
    capsule_x = (size - capsule_w) / 2
    capsule_y = size * 0.1
    painter.drawRoundedRect(
        int(capsule_x), int(capsule_y),
        int(capsule_w), int(capsule_h),
        int(capsule_w / 2), int(capsule_h / 4)
    )

    # Stem: line down from capsule
    stem_x = size / 2
    stem_y1 = capsule_y + capsule_h
    stem_y2 = size * 0.75
    painter.setOpacity(0.8)
    painter.drawLine(int(stem_x), int(stem_y1), int(stem_x), int(stem_y2))

    # Mute slash: if recording, omit this; if muted, add diagonal line
    # (kept simple — just the mic base is enough for now)

    painter.end()
    # erst nach dem Zeichnen setzen: der Painter arbeitet sonst in logischen
    # Koordinaten und der Glyph würde doppelt skaliert
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class TrayMenu(QSystemTrayIcon):
    """macOS menu bar tray icon with Quassel status and controls."""

    def __init__(self, app, on_open_center, on_quit, on_toggle=None,
                 on_panic=None, on_raw_back=None):
        super().__init__(app)
        self.app = app
        self.on_open_center = on_open_center
        self.on_quit = on_quit
        self.mode = "ready"  # ready, recording, off, error, transcribing
        self.secure_input = False

        # Create menu
        self.menu = QMenu()

        # Status label (non-clickable separator)
        self.status_action = self.menu.addAction(tr("tray_ready"))
        self.status_action.setEnabled(False)

        self.menu.addSeparator()

        # Toggle on/off (only when the app provides a controller callback)
        self.toggle_action = None
        if on_toggle is not None:
            self.toggle_action = self.menu.addAction(tr("turn_off"), on_toggle)

        # Not-Aus: beendet eine laufende Aufnahme auch dann, wenn der Hotkey
        # nicht mehr reagiert (schickt SIGUSR2 an den Daemon-Prozess).
        self.panic_action = None
        if on_panic is not None:
            self.panic_action = self.menu.addAction(tr("tray_panic"), on_panic)

        # „Rohtext zurück": statt eines weiteren globalen Hotkeys (Kollisionen)
        # ein Menüeintrag. Der Text liegt nur im Daemon; das Menü löst aus.
        self.raw_back_action = None
        if on_raw_back is not None:
            self.raw_back_action = self.menu.addAction(tr("raw_back"), on_raw_back)

        # Open control center
        self.open_action = self.menu.addAction(tr("tray_open_center"),
                                               self.on_open_center)

        # Quit
        self.quit_action = self.menu.addAction(tr("quit"), self.on_quit)

        self.setContextMenu(self.menu)

        # Load or create icon
        icon_path = os.path.join(
            os.path.dirname(__file__), "..", "assets", "quassel.svg"
        )
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
        else:
            pixmap = _create_mic_icon(22)
            icon = QIcon(pixmap)
        # Template-Icon: macOS färbt es passend zur Menüleiste (hell/dunkel)
        icon.setIsMask(True)

        self.setIcon(icon)
        self.show()

    def set_mode(self, mode, text=""):
        """Update status line based on app state.

        Args:
            mode: 'ready', 'recording', 'transcribing', 'done', 'error', 'off'
            text: optional detail text (ignored in status line)
        """
        self.mode = mode
        status = tr(STATUS_KEYS.get(mode, "tray_ready"))
        if self.secure_input and mode != "off":
            status += " · " + tr("tray_secure")
        self.status_action.setText(status)
        if self.toggle_action is not None:
            self.toggle_action.setText(
                tr("turn_on") if mode == "off" else tr("turn_off"))

    def refresh_secure_input(self, query=None):
        """Statuszeile um „Sichere Eingabe aktiv" ergänzen, solange sie an ist."""
        on = (query or secure_input_enabled)()
        if on != self.secure_input:
            self.secure_input = on
            self.set_mode(self.mode)
        return on

    def retranslate(self):
        """Nach einem Wechsel der App-Sprache alle Menütexte neu setzen."""
        if self.panic_action is not None:
            self.panic_action.setText(tr("tray_panic"))
        if self.raw_back_action is not None:
            self.raw_back_action.setText(tr("raw_back"))
        self.open_action.setText(tr("tray_open_center"))
        self.quit_action.setText(tr("quit"))
        self.set_mode(self.mode)


def start_tray(app, on_open_center, on_quit, on_toggle=None, on_panic=None,
               on_raw_back=None):
    """Initialize and show the macOS tray menu.

    Args:
        app: QApplication instance
        on_open_center: callable to open the control center
        on_quit: callable to quit the app
        on_toggle: optional callable to switch dictation on/off
        on_panic: optional callable to stop a running dictation at once
        on_raw_back: optional callable that asks the daemon to put the raw
            text of the last dictation on the clipboard

    Returns:
        TrayMenu instance (keep reference alive for menu to persist)
    """
    tray = TrayMenu(app, on_open_center, on_quit, on_toggle=on_toggle,
                    on_panic=on_panic, on_raw_back=on_raw_back)
    return tray


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)

    def dummy_open():
        print("Open center")

    def dummy_quit():
        app.quit()

    tray = start_tray(app, dummy_open, dummy_quit)
    sys.exit(app.exec())
