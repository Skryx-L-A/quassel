"""GTK-freie Qt-Pille für das portable Linux-Bundle.

Spiegelt die GTK-Pille (quassel/pill.py): grauer Punkt (aus), lila Punkt
(bereit), rot atmender Punkt (Aufnahme), … (Transkription), kurz das Ergebnis.
Liest state.json, pollt den Daemon-Status, Linksklick schaltet an/aus,
Rechtsklick öffnet das Kontrollzentrum.

Positionierung unten-mittig per move() — auf X11 exakt, auf Wayland je nach
Compositor (Qt-Clients dürfen sich auf Wayland nicht global positionieren;
dafür nutzt die native Installation gtk4-layer-shell). Kern-Diktat ist davon
unberührt — die Pille ist nur der Indikator.
"""
import math
import os
import subprocess
import sys
import time

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from . import config
from .state import state_read

RESULT_SHOW_S = 3.0
CENTER_CMD = os.environ.get("QUASSEL_CENTER_CMD", "quassel-type")

ICON_PATHS = [
    os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/quassel-voice.svg"),
    os.path.join(os.path.dirname(__file__), "..", "assets", "quassel.svg"),
]


def app_icon():
    for p in ICON_PATHS:
        if os.path.exists(p):
            return QIcon(p)
    return QIcon.fromTheme("quassel-voice")

# Direction B (Lokal): Pine-Akzent, gedämpftes Grau (aus), Bernstein (Fehler).
WAVE_PINE = QColor("#34C18C")
WAVE_GRAY = QColor("#6A786F")
WAVE_AMBER = QColor("#E9A93A")
PILL_BG = QColor(17, 32, 26)
C_LABEL = QColor("#E7F0EB")
C_TEXT = QColor("#BAC8C0")


def daemon_active():
    if os.name == "nt":
        return True
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", "quasseld"],
                          check=False).returncode == 0


class Pill(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.cfg = config.Cfg()
        self.text = ""
        self.last_ts = None
        self.result_until = 0.0
        self.last_poll = 0.0
        self.on = daemon_active()
        self.mode = "ready" if self.on else "off"
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(80)
        self.cfg_timer = QTimer(self)
        self.cfg_timer.timeout.connect(self.reload_cfg)
        self.cfg_timer.start(1000)
        self.resize_to_cfg()
        self.setVisible(self.cfg.pill_enabled)

    def _scale(self):
        return max(0.6, min(2.0, self.cfg.pill_scale))

    def _op(self):
        return max(0.15, min(1.0, self.cfg.pill_opacity))

    def resize_to_cfg(self):
        s = self._scale()
        self.setFixedSize(int(240 * s), int(124 * s))
        self.reposition()

    def reposition(self):
        scr = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.x() + (g.width() - self.width()) // 2,
                  g.y() + g.height() - self.height() - int(36 * self._scale()))

    def reload_cfg(self):
        if self.cfg.reload():
            self.resize_to_cfg()
        self.setVisible(self.cfg.pill_enabled)

    def set_mode(self, mode, text=""):
        self.mode = mode
        self.text = text
        if mode in ("done", "error"):
            self.result_until = time.monotonic() + RESULT_SHOW_S
        self.update()

    def tick(self):
        now = time.monotonic()
        if now - self.last_poll > 2.0:
            self.last_poll = now
            on = daemon_active()
            if on != self.on:
                self.on = on
                if self.mode in ("off", "ready"):
                    self.set_mode("ready" if on else "off")
        st = state_read()
        if self.on and st.get("ts") != self.last_ts:
            self.last_ts = st.get("ts")
            s = st.get("state", "idle")
            self.set_mode("ready" if s == "idle" else s, st.get("text", ""))
        if self.mode in ("done", "error") and now > self.result_until:
            self.set_mode("ready" if self.on else "off")
        if self.mode == "recording":
            self.update()   # Atmungs-Animation

    # ----------------------------------------------------------- Zeichnen
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s, op = self._scale(), self._op()
        pill_h = int(28 * s)
        wave_w = 22 * s
        pad = 13 * s
        pill_w = pad * 2 + wave_w
        cx = self.width() / 2
        pill = QRectF(cx - pill_w / 2, self.height() - pill_h - 2, pill_w, pill_h)
        path = QPainterPath()
        path.addRoundedRect(pill, pill_h / 2, pill_h / 2)
        bg = QColor(PILL_BG)
        bg.setAlphaF(op)
        p.fillPath(path, bg)
        self._draw_wave(p, pill.left() + pad, pill.center().y(), wave_w, 14 * s)
        if self.cfg.pill_preview and self.text and self.mode in ("recording", "done", "error"):
            self._draw_bubble(p, pill, s, op)

    def _wave_color(self):
        if self.mode == "off":
            return WAVE_GRAY
        if self.mode == "error":
            return WAVE_AMBER
        return WAVE_PINE        # ready / recording / transcribing / done

    def _draw_wave(self, p, x, cy, w, h):
        """Fünf Balken — bewegen sich nur bei Aufnahme, sonst ruhend."""
        animating = self.mode == "recording"
        rest = [0.30, 0.46, 0.38, 0.52, 0.34]
        n = 5
        gap = w * 0.11
        bw = (w - gap * (n - 1)) / n
        t = time.monotonic()
        p.setPen(Qt.NoPen)
        p.setBrush(self._wave_color())
        for i in range(n):
            frac = (0.22 + 0.78 * (0.5 + 0.5 * math.sin(t * 7.5 + i * 1.1))) \
                if animating else rest[i]
            bh = max(2.0, h * frac)
            bx = x + i * (bw + gap)
            p.drawRoundedRect(QRectF(bx, cy - bh / 2, bw, bh), bw / 2, bw / 2)

    def _draw_bubble(self, p, pill, s, op):
        txt = self.text if len(self.text) <= 140 else "…" + self.text[-139:]
        f = QFont()
        f.setPixelSize(int(11 * s))
        p.setFont(f)
        margin = int(12 * s)
        avail = QRectF(margin, int(6 * s), self.width() - 2 * margin,
                       self.height() - pill.height() - int(16 * s))
        flags = int(Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap)
        br = p.boundingRect(avail, flags, txt)
        pad = int(6 * s)
        box = br.adjusted(-2 * pad, -pad, 2 * pad, pad)
        path = QPainterPath()
        path.addRoundedRect(box, 10, 10)
        bg = QColor(PILL_BG)
        bg.setAlphaF(op)
        p.fillPath(path, bg)
        p.setPen(C_TEXT)
        p.drawText(avail, flags, txt)

    # -------------------------------------------------------------- Klicks
    def mouseReleaseEvent(self, ev):
        # Linksklick öffnet (sicher) das Kontrollzentrum; An/Aus liegt auf dem
        # Rechtsklick — sonst beendet ein versehentlicher Klick neben dem Textfeld
        # unter der Pille mitten im Diktat ganz Quassel.
        if ev.button() == Qt.LeftButton:
            subprocess.Popen(CENTER_CMD.split())
        elif ev.button() == Qt.RightButton:
            self._toggle()

    def _toggle(self):
        if os.name == "nt":
            return
        if daemon_active():
            subprocess.run(["systemctl", "--user", "stop", "quasseld",
                            "quassel-server", "quassel-ydotoold"], check=False)
            self.on = False
            self.set_mode("off")
        else:
            subprocess.run(["systemctl", "--user", "start", "quasseld",
                            "quassel-server"], check=False)
            self.on = True
            self.set_mode("ready")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Quassel")
    # Fenster zuverlässig quassel.desktop zuordnen (Wayland/X11), damit Panel/
    # Taskleiste dasselbe Symbol wie das Fenster zeigen — nicht ein generisches.
    app.setDesktopFileName("quassel")
    app.setWindowIcon(app_icon())
    pill = Pill()
    pill.setWindowIcon(app_icon())
    pill.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
