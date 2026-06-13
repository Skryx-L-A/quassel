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
    os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/quassel.svg"),
    os.path.join(os.path.dirname(__file__), "..", "assets", "quassel.svg"),
]


def app_icon():
    for p in ICON_PATHS:
        if os.path.exists(p):
            return QIcon(p)
    return QIcon.fromTheme("quassel")

DOT_OFF = QColor("#5c5c66")
DOT_READY = QColor("#b9a7f5")
DOT_REC = QColor(255, 84, 84)
C_INFO = QColor("#e8e8ee")
C_DONE = QColor("#7ddf7d")
C_ERR = QColor("#ff8888")
C_TEXT = QColor("#cfcfd8")


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
        pill_w = int(46 * s)
        cx = self.width() / 2
        pill = QRectF(cx - pill_w / 2, self.height() - pill_h - 2, pill_w, pill_h)
        path = QPainterPath()
        path.addRoundedRect(pill, pill_h / 2, pill_h / 2)
        bg = QColor(16, 16, 22)
        bg.setAlphaF(op)
        p.fillPath(path, bg)
        self._draw_indicator(p, pill, s)
        if self.cfg.pill_preview and self.text and self.mode in ("recording", "done", "error"):
            self._draw_bubble(p, pill, s, op)

    def _draw_indicator(self, p, rect, s):
        cx, cy = rect.center().x(), rect.center().y()
        if self.mode == "recording":
            breathe = 0.5 + 0.5 * math.sin(time.monotonic() * 2 * math.pi / 2.4)
            r = (7 * s) * (0.82 + 0.14 * breathe)
            col = QColor(DOT_REC)
            col.setAlphaF(0.65 + 0.35 * breathe)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
            return
        sym = {"transcribing": ("…", C_INFO), "done": ("✓", C_DONE),
               "error": ("✕", C_ERR)}.get(self.mode)
        if sym:
            f = QFont()
            f.setPixelSize(int(13 * s))
            p.setFont(f)
            p.setPen(sym[1])
            p.drawText(QRectF(cx - 14 * s, cy - 14 * s, 28 * s, 28 * s),
                       Qt.AlignCenter, sym[0])
            return
        col = DOT_READY if self.mode == "ready" else DOT_OFF
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        r = 5 * s
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

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
        bg = QColor(16, 16, 22)
        bg.setAlphaF(op)
        p.fillPath(path, bg)
        p.setPen(C_TEXT)
        p.drawText(avail, flags, txt)

    # -------------------------------------------------------------- Klicks
    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._toggle()
        elif ev.button() == Qt.RightButton:
            subprocess.Popen(CENTER_CMD.split())

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
