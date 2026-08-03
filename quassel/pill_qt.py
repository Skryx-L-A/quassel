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

from PySide6.QtCore import Qt, QTimer, QRectF, QPoint
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from . import config
from .state import state_read

RESULT_SHOW_S = 3.0
# Taktstufen des Timers: schnell nur, solange etwas animiert (Aufnahme,
# Ergebnisfenster) — der Rest der Zeit reicht der langsame Takt, die Pille
# zeigt sonst nur einen ruhenden Zustand. Nicht langsamer als 250 ms: die
# Pille ist die Rückmeldung "Aufnahme läuft", mehr Verzug wäre spürbar.
TICK_FAST_MS = 80
TICK_SLOW_MS = 250
# daemon_active() zwischenspeichern: unter Linux startet jeder Aufruf einen
# systemctl-Prozess, bei 80ms-Takt sonst 43.200 Prozesse pro Tag.
DAEMON_ACTIVE_CACHE_S = 10.0
_daemon_active_cache = {"ts": float("-inf"), "val": None}
# Argv-Liste (kein String): Pfade mit Leerzeichen (z.B. ein Python in einem
# .app-Bundle) dürfen den Start des Kontrollzentrums nicht brechen.
CENTER_CMD = os.environ.get("QUASSEL_CENTER_CMD", "quassel-type").split()
# In-Prozess-Hooks (macOS-Menüleisten-App): Kontrollzentrum/An-Aus laufen dort
# über die MacApp statt über Kindprozess bzw. systemctl.
OPEN_CENTER = None
TOGGLE = None

ICON_PATHS = [
    os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/quassel-voice.svg"),
    os.path.join(os.path.dirname(__file__), "..", "assets", "quassel.svg"),
]


def app_icon():
    for p in ICON_PATHS:
        if os.path.exists(p):
            return QIcon(p)
    if sys.platform != "darwin":
        return QIcon.fromTheme("quassel-voice")
    return QIcon()

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
    if sys.platform == "darwin":
        return True
    now = time.monotonic()
    if now - _daemon_active_cache["ts"] < DAEMON_ACTIVE_CACHE_S:
        return _daemon_active_cache["val"]
    val = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "quasseld"],
                         check=False).returncode == 0
    _daemon_active_cache["ts"] = now
    _daemon_active_cache["val"] = val
    return val


class Pill(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            # macOS versteckt Qt.Tool-Fenster, sobald die App inaktiv ist —
            # eine Menüleisten-App ist nie aktiv, die Pille wäre unsichtbar.
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.cfg = config.Cfg()
        self.text = ""
        self.last_ts = None
        self.result_until = 0.0
        self.last_poll = 0.0
        self.on = daemon_active()
        self.mode = "ready" if self.on else "off"
        # Ziehen: globale Mausposition + Fenster-Position bei mousePressEvent,
        # _dragging erst ab der 4px-Schwelle (sonst wird jeder Klick zum Mini-Zug)
        self._drag_start = None
        self._drag_origin = None
        self._dragging = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(self._tick_interval())
        self.cfg_timer = QTimer(self)
        self.cfg_timer.timeout.connect(self.reload_cfg)
        self.cfg_timer.start(1000)
        self.resize_to_cfg()
        self._update_cursor()
        self.setVisible(self.cfg.pill_enabled)
        if not self.cfg.pill_enabled:
            self.timer.stop()

    def showEvent(self, ev):
        super().showEvent(ev)
        if sys.platform == "darwin":
            self._mac_float_everywhere()

    def _mac_float_everywhere(self):
        """Pille auf JEDEM Space und über Vollbild-Apps zeigen. Qt setzt nur
        das normale Floating-Level; erst CanJoinAllSpaces+FullScreenAuxiliary
        am NSWindow lassen sie beim Space-/Fenster-Wechsel mitkommen."""
        if QApplication.platformName() != "cocoa":
            return          # z.B. offscreen in Tests: winId ist kein NSView
        try:
            import objc
            from AppKit import (NSStatusWindowLevel,
                                NSWindowCollectionBehaviorCanJoinAllSpaces,
                                NSWindowCollectionBehaviorFullScreenAuxiliary,
                                NSWindowCollectionBehaviorStationary)
            view = objc.objc_object(c_void_p=int(self.winId()))
            win = view.window()
            win.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary)
            win.setLevel_(NSStatusWindowLevel)
        except Exception as e:  # noqa: BLE001 — Pille darf nie am Overlay sterben
            print(f"pill: NSWindow-Verhalten nicht setzbar: {e}",
                  file=sys.stderr, flush=True)

    def _scale(self):
        return max(0.6, min(2.0, self.cfg.pill_scale))

    def _op(self):
        return max(0.15, min(1.0, self.cfg.pill_opacity))

    def _bg_color(self):
        """Ovalhintergrund mit dem Transparenz-Regler als Alpha — die Balken
        (siehe _wave_color) bleiben davon unberührt, immer voll deckend."""
        bg = QColor(PILL_BG)
        bg.setAlphaF(self._op())
        return bg

    def resize_to_cfg(self):
        s = self._scale()
        self.setFixedSize(int(240 * s), int(124 * s))
        self.reposition()

    def reposition(self):
        """Bei aktiviertem Verschieben eine gespeicherte Position benutzen —
        aber nur, wenn sie noch auf einem verfügbaren Bildschirm liegt (Monitor
        abgezogen, Auflösung geändert). Sonst die automatische Position unten
        mittig; die Pille darf nie außerhalb aller Bildschirme landen."""
        if self.cfg.pill_movable and self._stored_pos_valid():
            self.move(self.cfg.pill_pos_x, self.cfg.pill_pos_y)
            return
        self._reposition_auto()

    def _reposition_auto(self):
        scr = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.x() + (g.width() - self.width()) // 2,
                  g.y() + g.height() - self.height() - int(36 * self._scale()))

    def _stored_pos_valid(self):
        x, y = self.cfg.pill_pos_x, self.cfg.pill_pos_y
        if x < 0 or y < 0:
            return False
        return any(scr.availableGeometry().contains(QPoint(x, y))
                   for scr in QApplication.screens())

    def _update_cursor(self):
        if not self.cfg.pill_movable:
            self.unsetCursor()
        elif self._dragging:
            self.setCursor(Qt.ClosedHandCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    def reload_cfg(self):
        if self.cfg.reload():
            self.resize_to_cfg()
            self._update_cursor()
            self.update()  # sonst wirkt z.B. der Transparenz-Regler erst beim nächsten Statuswechsel
        self.setVisible(self.cfg.pill_enabled)
        if self.cfg.pill_enabled:
            if not self.timer.isActive():
                self.timer.start(self._tick_interval())
        else:
            self.timer.stop()

    def _tick_interval(self):
        """80ms nur, solange sich etwas bewegt (Aufnahme oder ein noch
        laufendes Ergebnisfenster) — sonst reicht der langsame Takt."""
        if self.mode == "recording" or time.monotonic() < self.result_until:
            return TICK_FAST_MS
        return TICK_SLOW_MS

    def _apply_tick_interval(self):
        wanted = self._tick_interval()
        if self.timer.interval() != wanted:
            self.timer.setInterval(wanted)

    def set_mode(self, mode, text=""):
        self.mode = mode
        self.text = text
        if mode in ("done", "error"):
            self.result_until = time.monotonic() + RESULT_SHOW_S
        self._apply_tick_interval()
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
        self._apply_tick_interval()
        if self.mode == "recording":
            self.update()   # Atmungs-Animation

    # ----------------------------------------------------------- Zeichnen
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._scale()
        pill_h = int(28 * s)
        wave_w = 22 * s
        pad = 13 * s
        pill_w = pad * 2 + wave_w
        cx = self.width() / 2
        pill = QRectF(cx - pill_w / 2, self.height() - pill_h - 2, pill_w, pill_h)
        path = QPainterPath()
        path.addRoundedRect(pill, pill_h / 2, pill_h / 2)
        p.fillPath(path, self._bg_color())
        # Balken bleiben immer voll deckend — nur der Ovalhintergrund wird transparent
        self._draw_wave(p, pill.left() + pad, pill.center().y(), wave_w, 14 * s)
        if self.cfg.pill_preview and self.text and self.mode in ("recording", "done", "error"):
            self._draw_bubble(p, pill, s)

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

    def _draw_bubble(self, p, pill, s):
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
        p.fillPath(path, self._bg_color())
        p.setPen(C_TEXT)
        p.drawText(avail, flags, txt)

    # -------------------------------------------------------------- Klicks
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.cfg.pill_movable:
            self._drag_start = ev.globalPosition().toPoint()
            self._drag_origin = self.pos()
            self._dragging = False

    def mouseMoveEvent(self, ev):
        if self._drag_start is None:
            return
        delta = ev.globalPosition().toPoint() - self._drag_start
        if not self._dragging:
            if abs(delta.x()) < 4 and abs(delta.y()) < 4:
                return
            self._dragging = True
            self._update_cursor()
        self.move(self._drag_origin + delta)

    def mouseReleaseEvent(self, ev):
        # Linksklick öffnet (sicher) das Kontrollzentrum; An/Aus liegt auf dem
        # Rechtsklick — sonst beendet ein versehentlicher Klick neben dem Textfeld
        # unter der Pille mitten im Diktat ganz Quassel. Wurde tatsächlich
        # gezogen, öffnet der Release das Kontrollzentrum nicht.
        if ev.button() == Qt.LeftButton:
            was_dragging = self._dragging
            if was_dragging:
                self._save_position()
            self._drag_start = None
            self._dragging = False
            self._update_cursor()
            if not was_dragging and self.cfg.pill_click_opens_center:
                if OPEN_CENTER is not None:
                    OPEN_CENTER()
                else:
                    subprocess.Popen(list(CENTER_CMD))
        elif ev.button() == Qt.RightButton:
            self._toggle()

    def _save_position(self):
        pos = self.pos()
        config.save({("pill", "pos_x"): pos.x(), ("pill", "pos_y"): pos.y()})
        self.cfg.pill_pos_x = pos.x()
        self.cfg.pill_pos_y = pos.y()

    def _toggle(self):
        if TOGGLE is not None:
            TOGGLE()
            return
        if os.name == "nt" or sys.platform == "darwin":
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
