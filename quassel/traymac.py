"""macOS menu bar icon via QSystemTrayIcon.

Status line (non-clickable): Bereit/Aufnahme
Menu items: Kontrollzentrum öffnen, Beenden
Icon: monochromatic template-style for native macOS menu bar rendering.
"""
import os
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication


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

    def __init__(self, app, on_open_center, on_quit, on_toggle=None):
        super().__init__(app)
        self.app = app
        self.on_open_center = on_open_center
        self.on_quit = on_quit
        self.mode = "ready"  # ready, recording, off, error, transcribing

        # Create menu
        self.menu = QMenu()

        # Status label (non-clickable separator)
        self.status_action = self.menu.addAction("Bereit")
        self.status_action.setEnabled(False)

        self.menu.addSeparator()

        # Toggle on/off (only when the app provides a controller callback)
        self.toggle_action = None
        if on_toggle is not None:
            self.toggle_action = self.menu.addAction("Ausschalten", on_toggle)

        # Open control center
        self.menu.addAction("Kontrollzentrum öffnen", self.on_open_center)

        # Quit
        self.menu.addAction("Beenden", self.on_quit)

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
        status_map = {
            "ready": "Bereit",
            "recording": "Aufnahme",
            "transcribing": "Transkription",
            "done": "Fertig",
            "error": "Fehler",
            "off": "Aus",
        }
        self.status_action.setText(status_map.get(mode, "Bereit"))
        if self.toggle_action is not None:
            self.toggle_action.setText(
                "Einschalten" if mode == "off" else "Ausschalten")


def start_tray(app, on_open_center, on_quit, on_toggle=None):
    """Initialize and show the macOS tray menu.

    Args:
        app: QApplication instance
        on_open_center: callable to open the control center
        on_quit: callable to quit the app
        on_toggle: optional callable to switch dictation on/off

    Returns:
        TrayMenu instance (keep reference alive for menu to persist)
    """
    tray = TrayMenu(app, on_open_center, on_quit, on_toggle=on_toggle)
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
