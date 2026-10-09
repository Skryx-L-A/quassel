"""GTK-freie Qt-Pille für das portable Linux-Bundle.

Spiegelt die GTK-Pille (quassel/pill.py): fünf Balken, grau (aus), Pine
(bereit), bei Aufnahme der echte Mikrofonpegel aus state.json, eine flache
Linie mit Hinweis, wenn das Mikrofon nichts liefert; danach kurz das Ergebnis.
Pollt den Daemon-Status; Linksklick öffnet das Kontrollzentrum (abschaltbar),
Rechtsklick schaltet an/aus.

Positionierung unten-mittig per move() — auf X11 exakt, auf Wayland je nach
Compositor (Qt-Clients dürfen sich auf Wayland nicht global positionieren;
dafür nutzt die native Installation gtk4-layer-shell). Kern-Diktat ist davon
unberührt — die Pille ist nur der Indikator.
"""
import os
import subprocess
import sys
import time

from PySide6.QtCore import Qt, QTimer, QRectF, QPoint, QFileSystemWatcher
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from . import ask, config, profile
from .i18n import STRINGS, tr
from .state import LevelMeter, RUNDIR, level_read, state_read

RESULT_SHOW_S = 3.0
# Taktstufen des Timers: schnell nur, solange etwas animiert (Aufnahme,
# Ergebnisfenster) — der Rest der Zeit reicht der langsame Takt, die Pille
# zeigt sonst nur einen ruhenden Zustand.
# Der Ruhetakt bestimmt, wie schnell die Pille "Aufnahme läuft" zeigt, und das
# ist ihr einziger Zweck. Der Verzeichnis-Watcher weiter unten fängt den
# Zustandswechsel zwar zusätzlich ab, aber auf macOS gemessene 194-226 ms
# (FSEvents-Latenz, von Qt nicht einstellbar) — er ersetzt den Takt hier also
# nicht, er sichert ihn nur ab. 150 ms halbiert die Leerlauf-Weckvorgänge
# gegenüber 80 ms und bleibt im Mittel bei 75 ms Verzug, also nahe an dem,
# was vorher war. Auf Windows meldet der Watcher sofort.
TICK_FAST_MS = 80
TICK_SLOW_MS = 150
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
# Die Textblase bleibt lesbar, auch wenn der Transparenz-Regler die Pille
# selbst fast durchsichtig macht (Regler-Minimum 0,15).
BUBBLE_MIN_ALPHA = 0.85
# „Bewegung reduzieren" ändert sich selten; die Abfrage startet unter Linux
# einen Prozess und wird deshalb zwischengespeichert.
REDUCED_MOTION_CACHE_S = 30.0
_reduced_motion_cache = {"ts": float("-inf"), "val": False}
# Sichere Eingabe (macOS): einmal je Sekunde prüfen, beim Einschalten den
# Hinweis einige Sekunden zeigen — danach nur noch in der Menüleiste, damit
# ein dauerhaft geschütztes Terminal die Pille nicht stundenlang belegt.
SECURE_POLL_S = 1.0
SECURE_HINT_S = 4.0
STATUS_KEYS = {"off": "tray_off", "ready": "tray_ready",
               "recording": "tray_recording", "transcribing": "tray_transcribing",
               "done": "tray_done", "error": "tray_error"}


def _query_reduced_motion():
    """Systemeinstellung „Bewegung reduzieren" (macOS), „Animationen
    anzeigen" aus (Windows) bzw. GNOME enable-animations=false (Linux)."""
    if sys.platform == "darwin":
        from AppKit import NSWorkspace
        return bool(NSWorkspace.sharedWorkspace()
                    .accessibilityDisplayShouldReduceMotion())
    if os.name == "nt":
        import ctypes
        on = ctypes.c_int(1)
        SPI_GETCLIENTAREAANIMATION = 0x1042
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(on), 0)
        return not on.value
    out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface",
                          "enable-animations"], capture_output=True, text=True,
                         timeout=2, check=False).stdout
    return out.strip() == "false"


def secure_input_enabled():
    if sys.platform != "darwin":
        return False
    from .traymac import secure_input_enabled as query
    return query()


def reduced_motion():
    now = time.monotonic()
    if now - _reduced_motion_cache["ts"] >= REDUCED_MOTION_CACHE_S:
        try:
            val = _query_reduced_motion()
        except Exception:  # noqa: BLE001 — unbekannt heißt: normale Bewegung
            val = False
        _reduced_motion_cache.update(ts=now, val=val)
    return _reduced_motion_cache["val"]


def daemon_unit():
    """Diktat-Dienst dieser Instanz (mit QUASSEL_PROFILE quasseld@<profil>)."""
    return profile.service("quasseld")


def daemon_active():
    if os.name == "nt":
        return True
    if sys.platform == "darwin":
        return True
    now = time.monotonic()
    if now - _daemon_active_cache["ts"] < DAEMON_ACTIVE_CACHE_S:
        return _daemon_active_cache["val"]
    val = subprocess.run(["systemctl", "--user", "is-active", "--quiet", daemon_unit()],
                         check=False).returncode == 0
    _daemon_active_cache["ts"] = now
    _daemon_active_cache["val"] = val
    return val


def daemon_active_invalidate():
    """Cache verwerfen — nötig überall dort, wo der An/Aus-Zustand außerhalb
    von daemon_active() selbst geändert wird (_toggle() startet/stoppt die
    Dienste direkt). Sonst hält der 10s-Cache den ALTEN Wert fest, tick()
    sieht beim nächsten Poll on != self.on und dreht den gerade gesetzten
    Modus wieder zurück."""
    _daemon_active_cache["ts"] = float("-inf")
    _daemon_active_cache["val"] = None


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
        self.ask_text = ""      # wartende Frage eines Programms (G104)
        self.meter = LevelMeter()
        self.secure = False
        self.secure_until = 0.0
        self._painted_bubble = ""
        self.last_secure_poll = float("-inf")
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
        # Weckt tick() sofort bei einer Zustandsänderung (state_set() ersetzt
        # state.json atomar per os.replace) statt bis zu TICK_SLOW_MS zu
        # warten. Der Timer bleibt zusätzlich als Sicherheitsnetz bestehen —
        # FSEvents/kqueue können ein Ereignis verschlucken.
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(lambda _path: self.tick())
        self._watch_state_dir()
        self._update_accessible()
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

    def _watch_state_dir(self):
        """Beobachtet werden muss RUNDIR (das Verzeichnis), nicht state.json
        selbst: state_set() ersetzt die Datei atomar per os.replace, dabei
        verschwindet kurz der Ziel-Inode — ein Watch auf die Datei verliert
        sie dabei und meldet danach nichts mehr. RUNDIR kann beim Start der
        Pille noch fehlen (der Daemon hat state.json noch nie geschrieben);
        wir legen es hier selbst an (dasselbe exist_ok wie state_set()) statt
        nur auf ein späteres Nachziehen zu warten — so beobachtet der Watcher
        von Anfang an, ohne auf den ersten Daemon-Start zu warten."""
        if RUNDIR in self._fs_watcher.directories():
            return
        try:
            os.makedirs(RUNDIR, mode=0o700, exist_ok=True)
            self._fs_watcher.addPath(RUNDIR)
        except OSError:
            pass  # nächster tick()-Aufruf versucht es erneut

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
        if mode == "recording" and self.mode != "recording":
            self.meter.reset()       # neue Aufnahme: Pegelquelle erst abwarten
        self.mode = mode
        self.text = text
        self._update_accessible()
        if mode in ("done", "error"):
            self.result_until = time.monotonic() + RESULT_SHOW_S
        self._apply_tick_interval()
        self.update()

    def tick(self):
        self._watch_state_dir()   # billig (guard), holt ein zunächst fehlendes RUNDIR nach
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
        self._poll_secure_input(now)
        ask_text = ask.pill_line() if self.on else ""
        if ask_text != self.ask_text:
            self.ask_text = ask_text
            self._update_accessible()
        if self.bubble_text() != self._painted_bubble:
            self.update()       # Hinweis abgelaufen oder neu: neu zeichnen
        self._apply_tick_interval()
        if self.mode == "recording":
            self.meter.feed(level_read(st))
            desc = self.accessible_status()
            if desc != self.accessibleDescription():    # nur bei Änderung melden
                self.setAccessibleDescription(desc)
            self.update()   # Pegel bzw. Atmungs-Animation

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
        bubble = self._painted_bubble = self.bubble_text()
        if bubble:
            self._draw_bubble(p, pill, s, bubble)

    def _poll_secure_input(self, now):
        if now - self.last_secure_poll < SECURE_POLL_S:
            return
        self.last_secure_poll = now
        on = secure_input_enabled()
        if on and not self.secure:
            self.secure_until = now + SECURE_HINT_S
        if on != self.secure:
            self.secure = on
            self._update_accessible()
            self.update()

    def secure_hint(self, now=None):
        now = time.monotonic() if now is None else now
        return self.secure and self.on and now < self.secure_until

    def mic_silent(self):
        """Aufnahme läuft, der Pegel kommt an, aber seit 1,5 s ist nichts zu
        hören: stummgeschaltet, falsches Gerät oder keine Mikrofonfreigabe."""
        return self.mode == "recording" and self.meter.silent()

    def wake_listening(self):
        """Nimmt Quassel gerade die Äußerung nach der Wake-Phrase auf? Der
        Dienst schreibt dafür den festen Text "wake_listening" (in seiner
        Sprache) in den Zustand, nie diktierten Text."""
        return self.mode == "recording" and self.text in STRINGS["wake_listening"]

    def bubble_text(self):
        """Hinweis „kein Ton" geht vor (auch ohne Vorschau), dann „Sichere
        Eingabe" und „Hört zu…" nach der Wake-Phrase (auch ohne Vorschau,
        damit man sieht, dass Quassel zuhört), sonst der Vorschau- bzw.
        Ergebnistext, wenn eingeschaltet."""
        if self.mic_silent():
            return tr("mic_silent")
        if self.secure_hint() and self.mode in ("ready", "recording"):
            return tr("secure_input")
        if self.wake_listening():
            return tr("wake_listening")
        # Wartet ein Programm auf eine Antwort (G104), steht die Frage in der
        # Pille, auch ohne Vorschau: niemand soll versehentlich ins falsche
        # Ziel diktieren. Während der Antwort weicht sie der Live-Vorschau.
        if self.ask_text and (self.mode == "ready" or (
                self.mode == "recording" and not (self.cfg.pill_preview and self.text))):
            return self.ask_text
        if self.cfg.pill_preview and self.text and \
                self.mode in ("recording", "done", "error"):
            return self.text
        return ""

    def _wave_color(self):
        if self.mode == "off":
            return WAVE_GRAY
        if self.mode == "error" or self.mic_silent():
            return WAVE_AMBER
        return WAVE_PINE        # ready / recording / transcribing / done

    def wave_fracs(self):
        """Höhen der fünf Balken (0..1), siehe LevelMeter.wave_fracs."""
        return self.meter.wave_fracs(self.mode, calm=reduced_motion())

    def accessible_status(self):
        """Zustand als Text für Bildschirmleser — die Pille vermittelt ihn
        sonst nur über Farbe, Form und Bewegung."""
        parts = [tr(STATUS_KEYS.get(self.mode, "tray_ready"))]
        hint = self.bubble_text()
        if hint:
            parts.append(hint)
        return ": ".join(parts)

    def _update_accessible(self):
        self.setAccessibleName(tr("app_name"))
        self.setAccessibleDescription(self.accessible_status())

    def _draw_wave(self, p, x, cy, w, h):
        """Fünf Balken — bewegen sich nur bei Aufnahme, sonst ruhend."""
        fracs = self.wave_fracs()
        n = len(fracs)
        gap = w * 0.11
        bw = (w - gap * (n - 1)) / n
        p.setPen(Qt.NoPen)
        p.setBrush(self._wave_color())
        if self.mic_silent():
            # flache, durchgehende Linie: hier kommt nichts an
            lh = max(2.0, h * 0.14)
            p.drawRoundedRect(QRectF(x, cy - lh / 2, w, lh), lh / 2, lh / 2)
            return
        for i, frac in enumerate(fracs):
            bh = max(2.0, h * frac)
            bx = x + i * (bw + gap)
            p.drawRoundedRect(QRectF(bx, cy - bh / 2, bw, bh), bw / 2, bw / 2)

    def _draw_bubble(self, p, pill, s, text):
        txt = text if len(text) <= 140 else "…" + text[-139:]
        f = QFont()
        f.setPixelSize(int(11 * s))
        p.setFont(f)
        margin = int(12 * s)
        avail = QRectF(margin, int(6 * s), self.width() - 2 * margin,
                       self.height() - pill.height() - int(16 * s))
        flags = int(Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap)
        # Stift VOR boundingRect setzen: mit Qt.NoPen (von den Balken) liefert
        # Qt ein leeres Rechteck, und die Blase landete links oben statt
        # hinter dem Text — der Text stand dann ohne Grund auf dem Desktop.
        p.setPen(C_TEXT)
        br = p.boundingRect(avail, flags, txt)
        pad = int(6 * s)
        box = br.adjusted(-2 * pad, -pad, 2 * pad, pad)
        path = QPainterPath()
        path.addRoundedRect(box, 10, 10)
        bg = QColor(PILL_BG)
        bg.setAlphaF(max(BUBBLE_MIN_ALPHA, self._op()))
        p.fillPath(path, bg)
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
        own = [daemon_unit(), profile.service()]
        if daemon_active():
            # Ein Profil stoppt den geteilten ydotoold der installierten App nicht.
            shared = [] if profile.name() else ["quassel-ydotoold"]
            subprocess.run(["systemctl", "--user", "stop", *own, *shared], check=False)
            daemon_active_invalidate()
            self.on = False
            self.set_mode("off")
        else:
            from .platform_linux import import_session_env
            import_session_env()
            subprocess.run(["systemctl", "--user", "start", *own], check=False)
            daemon_active_invalidate()
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
