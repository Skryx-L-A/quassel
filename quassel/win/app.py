"""Quassel für Windows — alles in einem Qt-Prozess.

Tray-Icon (an/aus, Einstellungen, Beenden) + schwebende Pille + Hotkey-Hook
+ lokaler whisper-server.exe. Der Ablauf eines Diktats (Aufnahme,
Transkription, Einfügen, Rettung, Not-Aus) ist derselbe wie unter Linux und
macOS: quassel/dictation.py. Hier liegen nur die Windows-Anbindung
(WinDictation) und die Oberfläche.

Threads: der Raw-Input-Hook reiht Tasten ein, der Ereignis-Thread
(dictation.ChordPump) treibt die Chord-Maschine und stoppt Aufnahmen
synchron, der Aktions-Thread (dictation.ActionRunner) transkribiert und
fügt ein. Der Qt-Hauptthread zeichnet nur und bekommt Zustände per Signal.
"""
import getpass
import os
import re
import sys
import threading
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .. import config, dictation, i18n, pill_qt, power, profile, selfheal, whisperclient
from ..audio import wav_from_raw
from ..mediacontrol import AudioDucker
from ..i18n import tr
from ..state import LevelMeter, level_read
from . import server
from .audio_win import Recorder
from .hook import CHORDS_VK, KeyboardHook
from .paste import (clip_copy, clip_read, paste, send_backspaces, send_enter,
                    streaming_begin, streaming_restore, type_chunk)

DEBUG_LOG_MAX = 1_000_000   # ab ~1 MB eine Generation wegrotieren


def dlog(msg):
    """Timing-Protokoll für die Beta: %LOCALAPPDATA%/Quassel/debug.log.

    Wird die Datei zu groß, wandert sie nach debug.log.1 (eine Generation),
    damit das Log über Wochen nicht unbegrenzt wächst."""
    try:
        path = os.path.join(config.DATADIR, "debug.log")
        try:
            if os.path.getsize(path) > DEBUG_LOG_MAX:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %9.3f %s\n" % (time.strftime("%H:%M:%S"),
                                       time.monotonic(), msg))
    except OSError:
        pass
_MEI = getattr(sys, "_MEIPASS", None)   # PyInstaller-Bundle: Assets liegen dort
ICON_CANDIDATES = ([os.path.join(_MEI, "assets", "quassel.png"),
                    os.path.join(_MEI, "assets", "quassel.svg")] if _MEI else []) + [
    os.path.join(os.path.dirname(__file__), "..", "..", "assets", "quassel.svg"),
    os.path.join(os.path.dirname(__file__), "..", "..", "assets", "quassel.png"),
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



class _Bubble(QWidget):
    """Textblase über der Pille (Live-Vorschau / Ergebnis / Hinweise), wie auf
    Linux und macOS.

    Eigenes Top-Level-Fenster, damit die Pille darunter fest verankert
    bleibt, wenn die Blase wächst oder schrumpft. Farben wie die Pille
    (Direction B); die Blase bleibt auch bei fast durchsichtiger Pille
    deckend genug zum Lesen (BUBBLE_MIN_ALPHA)."""

    def __init__(self, cfg):
        super().__init__(None,
                         Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.cfg = cfg
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setMaximumWidth(440)
        lay.addWidget(self.label)

    def bg_color(self):
        op = max(0.15, min(1.0, self.cfg.pill_opacity))
        bg = QColor(pill_qt.PILL_BG)
        bg.setAlphaF(max(pill_qt.BUBBLE_MIN_ALPHA, op))
        return bg

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(self.bg_color())
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 12, 12)

    def set_text(self, text, italic=False):
        """Nur Inhalt/Größe; sichtbar schalten übernimmt die Pille."""
        if not text:
            return False
        short = text if len(text) <= 140 else "…" + text[-139:]
        s = max(0.6, min(2.0, self.cfg.pill_scale))
        style = "color: %s; font-size: %dpx;" % (pill_qt.C_TEXT.name(), int(11 * s))
        if italic:
            style += " font-style: italic;"
        self.label.setStyleSheet(style)
        self.label.setText(short)
        self.adjustSize()
        return True


class _PillBody(QWidget):
    """Die eigentliche Pille: fünf Balken in der Direction-B-Palette, gleiche
    Optik und Logik wie die Pillen unter Linux und macOS (state.LevelMeter):
    echter Mikrofonpegel bei Aufnahme, flache Bernstein-Linie bei Stille,
    eigene Form je Zustand, ruhig bei „Animationen aus"."""

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.mode = "ready"
        self.meter = LevelMeter()
        self.resize_to_cfg()

    def resize_to_cfg(self):
        s = max(0.6, min(2.0, self.cfg.pill_scale))
        self.setFixedSize(int(48 * s), int(28 * s))

    def mic_silent(self):
        return self.mode == "recording" and self.meter.silent()

    def _wave_color(self):
        if self.mode == "off":
            return pill_qt.WAVE_GRAY
        if self.mode == "error" or self.mic_silent():
            return pill_qt.WAVE_AMBER
        return pill_qt.WAVE_PINE        # ready / recording / transcribing / done

    def wave_fracs(self):
        return self.meter.wave_fracs(self.mode, calm=pill_qt.reduced_motion())

    def paintEvent(self, _ev):
        s = max(0.6, min(2.0, self.cfg.pill_scale))
        op = max(0.15, min(1.0, self.cfg.pill_opacity))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor(pill_qt.PILL_BG)
        bg.setAlphaF(op)
        p.setBrush(bg)
        p.setPen(QColor(255, 255, 255, 13))
        radius = self.height() / 2 - 1
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)
        p.setPen(Qt.NoPen)
        p.setBrush(self._wave_color())
        wave_w, wave_h = 22 * s, 14 * s
        x0 = (self.width() - wave_w) / 2
        cy = self.height() / 2
        if self.mic_silent():
            lh = max(2.0, wave_h * 0.14)      # flache Linie: nichts kommt an
            p.drawRoundedRect(QRectF(x0, cy - lh / 2, wave_w, lh), lh / 2, lh / 2)
            return
        fracs = self.wave_fracs()
        n = len(fracs)
        gap = wave_w * 0.11
        bw = (wave_w - gap * (n - 1)) / n
        for i, frac in enumerate(fracs):
            bh = max(2.0, wave_h * frac)
            bx = x0 + i * (bw + gap)
            p.drawRoundedRect(QRectF(bx, cy - bh / 2, bw, bh), bw / 2, bw / 2)

    def set_mode(self, mode):
        if mode == "recording" and self.mode != "recording":
            self.meter.reset()          # neue Aufnahme: Pegelquelle abwarten
        self.mode = mode
        self.resize_to_cfg()
        self.update()


class Pill(QWidget):
    """Schwebendes Overlay unten-mittig auf dem Monitor mit dem Mauszeiger.
    Die Pille selbst ist fest verankert; die Textblase ist ein eigenes
    Fenster darüber und darf wachsen, ohne die Pille zu verschieben.

    level_source: Callable -> (Pegel, Zeitstempel) oder None; setzt WinApp."""

    def __init__(self):
        super().__init__(None,
                         Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.cfg = config.Cfg()
        self.mode = "ready"
        self.text = ""
        self.level_source = lambda: None
        self._silent_shown = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.body = _PillBody(self.cfg)
        lay.addWidget(self.body)
        self.bubble = _Bubble(self.cfg)
        self.setAccessibleName(tr("app_name"))
        self.set_mode("ready")
        anim = QTimer(self)
        anim.timeout.connect(self._tick)
        anim.start(80)
        # Wie auf Linux: Config sekündlich neu laden, damit Sichtbarkeit/
        # Größe/Transparenz aus den Einstellungen sofort wirken
        cfgtimer = QTimer(self)
        cfgtimer.timeout.connect(self._reload_cfg)
        cfgtimer.start(1000)

    def _reload_cfg(self):
        if not self.cfg.reload():
            return
        self.body.resize_to_cfg()
        self.adjustSize()
        self.setVisible(self.cfg.pill_enabled)
        if not self.cfg.pill_enabled:
            self.bubble.hide()
        self.body.update()
        self.reposition()

    def _tick(self):
        if self.mode == "recording":
            sample = self.level_source()
            level = None
            if sample is not None:
                level = level_read({"level": sample[0], "level_ts": sample[1]})
            self.body.meter.feed(level)
            silent = self.body.mic_silent()
            if silent != self._silent_shown:
                self._silent_shown = silent
                self._show_bubble()
            self.body.update()
        self.reposition()

    def bubble_text(self):
        """„Kein Ton vom Mikrofon" geht vor, sonst Vorschau bzw. Ergebnis."""
        if self.body.mic_silent():
            return tr("mic_silent")
        if self.mode in ("recording", "done", "error"):
            return self.text
        return ""

    def accessible_status(self):
        parts = [tr(pill_qt.STATUS_KEYS.get(self.mode, "tray_ready"))]
        if self.bubble_text():
            parts.append(self.bubble_text())
        return ": ".join(parts)

    def _show_bubble(self):
        text = self.bubble_text()
        italic = self.mode == "recording" and not self.body.mic_silent()
        has_text = self.bubble.set_text(text, italic=italic)
        self.setAccessibleDescription(self.accessible_status())
        self.bubble.setVisible(has_text and self.cfg.pill_enabled)
        self.reposition()

    def set_mode(self, mode, text=""):
        self.mode = mode
        self.text = text
        if mode == "recording" and self.body.mode != "recording":
            self._silent_shown = False
        self.body.set_mode(mode)
        self.adjustSize()
        self.setVisible(self.cfg.pill_enabled)
        self._show_bubble()

    def reposition(self):
        """Auf den Monitor mit dem Mauszeiger (= aktiver Bildschirm)."""
        cursor = self.cursor().pos()
        s = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        geo = s.availableGeometry()
        pos_x = geo.center().x() - self.width() // 2
        pos_y = geo.bottom() - self.height() - 40
        if self.pos().x() != pos_x or self.pos().y() != pos_y:
            self.move(pos_x, pos_y)
        if self.bubble.isVisible():
            bx = geo.center().x() - self.bubble.width() // 2
            by = pos_y - 8 - self.bubble.height()
            if self.bubble.pos().x() != bx or self.bubble.pos().y() != by:
                self.bubble.move(bx, by)


class WinDictation(dictation.DictationCore):
    """Windows-Anbindung der gemeinsamen Ablaufsteuerung. Läuft auf dem
    Ereignis- und dem Aktions-Thread; die Oberfläche erreicht sie nur über
    das Qt-Signal des Besitzers (thread-sicher)."""

    def __init__(self, owner):
        self.owner = owner              # WinApp: Signal, enabled, Pumpe
        self.cfg = owner.cfg
        self.rec = Recorder(preroll_ms=getattr(self.cfg, "preroll_ms", 0))
        self.partial = None
        self.last_paste_len = 0
        self.streamer = None
        self._clip_backup = None
        self.ducker = AudioDucker()     # Musik/Ton beim Diktieren leise schalten
        self.wake = None
        self._runner = None             # setzt WinApp, sobald die Threads stehen

    # ------------------------------------------------- Plattform-Anbindung
    def _state(self, state, text=""):
        if state == "idle":
            state = "ready" if self.owner.enabled else "off"
        self.owner.sig_state.emit(state, text)

    def _notify(self, text):
        # Windows meldet Abbruch und "zu kurz" nur über die Pille (wie bisher);
        # der Text landet im Debug-Log.
        dlog("notify: " + text)

    def _log(self, msg):
        dlog(msg)

    def _paste(self, text):
        return paste(text)

    def _send_backspaces(self, n):
        send_backspaces(n)

    def _send_enter(self):
        send_enter()

    def _type_chunk(self, chunk):
        type_chunk(chunk)

    def _streaming_begin(self):
        return streaming_begin()

    def _streaming_restore(self, backup):
        streaming_restore(backup)

    def _wav_from_raw(self, data, path):
        wav_from_raw(data, path)

    def _new_recorder(self, raw_path):
        return Recorder(raw_path=raw_path)

    def _server_vad(self):
        return server.vad_model_path() is not None

    def _clip_copy(self, text):
        return clip_copy(text)

    def _level_target(self):
        # Pille läuft im selben Prozess: Pegel direkt an WinApp (thread-sicher,
        # nur ein Tupel wird ersetzt), kein Umweg über eine Datei.
        return getattr(self.owner, "set_level", None)

    def _clip_read(self):
        # Hält die Ablage nach einem gescheiterten Einfügen das Diktat? Dann
        # stimmt „liegt in der Zwischenablage", sonst wird gerettet.
        return clip_read()

    def _report_mic_missing(self):
        # Nicht still scheitern: sonst bleibt die Pille grau und der Nutzer
        # weiß nicht, warum nichts passiert (Mikro belegt, aus oder weg).
        self._state("error", self._mic_error_text())

    def _reset_hotkey(self):
        pump = getattr(self.owner, "pump", None)
        if pump is not None:
            pump.force_reset()

    def _wake_busy(self):
        return self._busy() or not self.owner.enabled


class WinApp(QObject):
    sig_state = Signal(str, str)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = config.Cfg()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        whisperclient.STARTER = server.start          # Server-Start = Windows-Variante
        original_recover = selfheal.recover
        selfheal.recover = lambda: original_recover(restart=server.restart_owned)
        self.enabled = True
        self.core = WinDictation(self)

        self._level = None              # (Pegel, Zeitstempel) der laufenden Aufnahme
        self.pill = Pill()
        self.pill.level_source = lambda: self._level
        self.sig_state.connect(self._apply_state)

        self.hook = KeyboardHook()
        self.hook.start()
        self.runner = dictation.ActionRunner(name="quassel-actions", log=dlog)
        self.core._runner = self.runner
        ga, gb = CHORDS_VK[self.cfg.chord]
        self.pump = dictation.ChordPump(
            self.hook.events, self.core, self.runner, ga, gb,
            self.cfg.hold_min, self.cfg.double_window,
            is_enabled=lambda: self.enabled, log=dlog)
        self.pump.start()
        self.power_events = power.PowerEvents(self._on_sleep, self._on_wake)
        self.power_filter = _PowerMessageFilter(self.power_events)
        app.installNativeEventFilter(self.power_filter)

        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip("Quassel")
        self.build_menu()
        self.tray.show()

        # Hotkey-Wechsel aus den Einstellungen live übernehmen
        self.cfgpoll = QTimer()
        self.cfgpoll.timeout.connect(self.reload_chord)
        self.cfgpoll.start(1000)

        # Einzelinstanz-Kanal: weitere Starts melden sich hier und wir
        # öffnen stattdessen das Einstellungsfenster. Name je Benutzer und
        # Sitzung, Zugriff nur für den eigenen Benutzer (Audit A11).
        name = ipc_name()
        QLocalServer.removeServer(name)
        self.ipc = QLocalServer()
        self.ipc.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.ipc.newConnection.connect(self.on_second_instance)
        self.ipc.listen(name)

        if not server.installed():
            threading.Thread(target=self.first_run_setup, daemon=True).start()

    @property
    def rec(self):
        return self.core.rec

    def raw_back(self):
        """„Rohtext zurück" aus dem Kontrollzentrum (läuft im selben Prozess):
        auf dem Aktions-Thread, hinter einem noch laufenden Diktat."""
        self.runner.submit("raw-back", self.core.raw_back)
        return True

    def set_level(self, level):
        """Von der Pegelpumpe (eigener Thread) aufgerufen."""
        self._level = (level, time.time())

    def on_second_instance(self):
        sock = self.ipc.nextPendingConnection()
        if sock:
            sock.disconnectFromServer()
        self.open_settings()

    # ----------------------------------------------------------- Ersteinrichtung
    def first_run_setup(self):
        """Erstausstattung beim ersten Start: Modelle + Engine bereitstellen
        (aus dem Offline-Bundle, falls vorhanden, sonst Download). Die Pille
        zeigt, welche Datei gerade vorbereitet wird."""
        self.sig_state.emit("transcribing", tr("preparing", item="…"))
        last = {"what": ""}

        def progress(_frac, what=""):
            if what and what != last["what"]:
                last["what"] = what
                self.sig_state.emit("transcribing", tr("preparing", item=what))
        try:
            # Offline-Bundle vorhanden -> alles lokal uebernehmen (volle Offline-
            # Nutzung); sonst schlank (nur passende Engine + ein Modell laden).
            server.provision(progress, full=server.bundle_dir() is not None)
            if server.ensure_working(progress):
                self.sig_state.emit("done", tr("ready"))
            else:
                self.sig_state.emit("error", dictation.failure_text(None))
        except Exception as e:  # noqa: BLE001
            # Rohe Ausnahmetexte gehören ins Log, nicht in die Pille (H113).
            dlog("setup: %r" % e)
            self.sig_state.emit("error", tr("download_failed", err=type(e).__name__))


    # ------------------------------------------------------------------ Menü
    def build_menu(self):
        menu = QMenu()
        self.act_toggle = QAction(tr("turn_off"), menu)
        self.act_toggle.triggered.connect(self.toggle)
        menu.addAction(self.act_toggle)
        self.act_panic = QAction(_panic_label(), menu)
        self.act_panic.triggered.connect(self.panic)
        menu.addAction(self.act_panic)
        # „Rohtext zurück": Menüeintrag statt eines weiteren globalen Hotkeys.
        self.act_raw_back = QAction(tr("raw_back"), menu)
        self.act_raw_back.triggered.connect(self.raw_back)
        menu.addAction(self.act_raw_back)
        self.act_settings = QAction(tr("settings"), menu)
        self.act_settings.triggered.connect(self.open_settings)
        menu.addAction(self.act_settings)
        menu.addSeparator()
        self.act_quit = QAction(tr("quit"), menu)
        self.act_quit.triggered.connect(self.quit)
        menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.open_settings()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)

    def retranslate(self):
        """App-Sprache ohne Neustart übernehmen (vom Einstellungsfenster
        aufgerufen, wenn die UI-Sprache gewechselt wird)."""
        self.cfg.reload()
        i18n.set_language(None if self.cfg.ui_language == "auto"
                          else self.cfg.ui_language)
        self.act_toggle.setText(tr("turn_off") if self.enabled else tr("turn_on"))
        self.act_panic.setText(_panic_label())
        self.act_raw_back.setText(tr("raw_back"))
        self.act_settings.setText(tr("settings"))
        self.act_quit.setText(tr("quit"))
        self.tray.setToolTip("Quassel")

    def open_settings(self):
        # Das Qt-Kontrollzentrum (center.py) ist plattformneutral nutzbar.
        try:
            from ..center import Center
            if not hasattr(self, "_settings") or self._settings is None:
                self._settings = Center(controller=self)
            self._settings.show()
            self._settings.raise_()
            self._settings.activateWindow()
        except Exception:  # noqa: BLE001 — sichtbar machen statt still scheitern
            import traceback
            d = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Quassel")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "crash.log"), "a", encoding="utf-8") as f:
                f.write("\n--- settings ---\n" + traceback.format_exc())

    def toggle(self):
        self.enabled = not self.enabled
        self.act_toggle.setText(tr("turn_off") if self.enabled else tr("turn_on"))
        if not self.enabled:
            # Abbruch auf dem Ereignis-Thread, der auch startet und stoppt —
            # nie quer aus dem Qt-Thread in eine laufende Aufnahme.
            def cancel():
                if self.core.rec.active:
                    self.core.cancel_recording("canceled_key")
                self.pump.force_reset()
            self.pump.post(cancel)
        self.sig_state.emit("ready" if self.enabled else "off", "")

    def panic(self):
        """Not-Aus aus dem Tray: beendet eine laufende Aufnahme sofort und
        verwirft deren Text, auch wenn Transkription oder Einfügen hängen."""
        threading.Thread(target=self.core.panic_stop, daemon=True,
                         name="quassel-panic").start()

    def quit(self):
        self.app.removeNativeEventFilter(self.power_filter)
        self.pump.stop()
        self.runner.stop()
        self.hook.stop()
        server.stop()
        self.app.quit()

    def _on_sleep(self):
        self.pump.force_reset()
        if self.core._busy():
            threading.Thread(target=self.core.panic_stop, daemon=True,
                             name="quassel-sleep-stop").start()

    def _on_wake(self):
        threading.Thread(target=selfheal.recover, daemon=True,
                         name="quassel-wake-check").start()


    # --------------------------------------------------------------- Hotkey
    def reload_chord(self):
        """Geänderten Chord aus der Config übernehmen (greift erst im
        Ruhezustand — die Maschine würde sonst ihren Zustand verlieren)."""
        self.cfg.reload()
        ga, gb = CHORDS_VK[self.cfg.chord]
        self.pump.reconfigure(ga, gb, self.cfg.hold_min, self.cfg.double_window)
        self.core._sync_wakeword()  # Wake-Word nach Konfig starten/stoppen

    def _apply_state(self, state, text):
        self.pill.cfg.reload()
        self.pill.set_mode(state, text)
        if state in ("done", "error"):
            QTimer.singleShot(3000, lambda: self.pill.set_mode(
                "ready" if self.enabled else "off"))


class _PowerMessageFilter(QAbstractNativeEventFilter):
    """Forward WM_POWERBROADCAST from Qt's existing Windows message loop."""

    def __init__(self, events):
        super().__init__()
        self.events = events

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            self.events.windows_message(msg.message, msg.wParam)
        return False, 0


def _session_id():
    """Windows-Sitzungsnummer dieses Prozesses (schneller Benutzerwechsel,
    Remotedesktop); None, wenn nicht ermittelbar."""
    try:
        import ctypes
        sid = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
            return sid.value
    except Exception:  # noqa: BLE001 — ohne Nummer bleibt der Benutzername
        pass
    return None


def ipc_name(user=None, session=None):
    """Name des Einzelinstanz-Kanals. QLocalServer legt unter Windows eine
    Named Pipe im globalen Namensraum an: ein fester Name galt für alle
    Benutzer und Sitzungen, und beim schnellen Benutzerwechsel beendete sich
    die Instanz des zweiten Nutzers sofort. Benutzer, Sitzung und Profil
    machen den Namen eindeutig."""
    if user is None:
        try:
            user = os.environ.get("USERNAME") or getpass.getuser()
        except Exception:  # noqa: BLE001
            user = ""
    if session is None:
        session = _session_id()
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", user or "user")[:64]
    name = "quassel-app%s-%s" % (profile.suffix(), safe)
    if session is not None:
        name += "-s%d" % session
    return name


def _panic_label():
    return "Diktat sofort beenden" if i18n.current() == "de" else "Stop dictation now"


def run_setup(full=False):
    """Vom Installer aufgerufen (--setup): stellt mit Fortschrittsfenster die
    Sprach-Engine + ein Modell bereit. Standardmaessig SCHLANK (nur die zur
    Hardware passende Engine + EIN Modell per hwdetect). Mit --setup --all
    werden alle Engines + alle 5 Modelle geladen (volle Offline-Nutzung).
    Quelle ist ein Offline-Bundle, falls vorhanden, sonst Download."""
    from PySide6.QtWidgets import QProgressDialog
    app = QApplication([])
    app.setWindowIcon(app_icon())
    dlg = QProgressDialog("", None, 0, 100)
    dlg.setWindowTitle("Quassel Setup")
    dlg.setLabelText(tr("preparing", item="whisper"))
    dlg.setMinimumWidth(440)
    dlg.setCancelButton(None)
    dlg.show()
    state = {"frac": 0.0, "what": ""}
    worker = {}

    def progress(frac, what=""):
        state["frac"] = frac
        if what:
            state["what"] = what

    def work():
        try:
            server.provision(progress, full=full)
            server.ensure_working(progress)
            server.stop()              # App startet ihn bei Bedarf selbst
        except Exception:  # noqa: BLE001 — App holt Fehlendes beim 1. Start nach
            pass
        worker["done"] = True

    threading.Thread(target=work, daemon=True).start()

    def tick():
        dlg.setValue(int(state["frac"] * 100))
        if state["what"]:
            dlg.setLabelText(tr("preparing", item=state["what"]))
        if worker.get("done"):
            app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(120)
    app.exec()


def audiocheck():
    """Diagnose (--audiocheck): schreibt nach DATADIR/audiocheck.json, welche
    Audio-Ducking-Backends verfügbar sind, und probt einmal die Medien-
    Enumeration. Nur für Support/Build-Verifikation — verändert nichts."""
    import json
    from . import audioctl
    info = {"have_pycaw": audioctl._HAVE_PYCAW, "have_smtc": audioctl._HAVE_SMTC}
    try:
        tok = audioctl.duck_apply("music")      # pausiert nichts, wenn nichts spielt
        audioctl.duck_restore("music", tok)
        info["music_token"] = tok
    except Exception as e:  # noqa: BLE001
        info["music_error"] = str(e)[:200]
    try:
        os.makedirs(config.DATADIR, exist_ok=True)
        with open(os.path.join(config.DATADIR, "audiocheck.json"), "w",
                  encoding="utf-8") as f:
            json.dump(info, f)
    except OSError:
        pass


def main():
    if os.name != "nt":
        raise SystemExit("quassel.win.app läuft nur unter Windows.")
    # Eigene AppUserModelID setzen, sonst gruppiert Windows unter dem Host-
    # python.exe und zeigt dessen Symbol in der Taskleiste statt unseres.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Quassel.VoiceTyping")
    except Exception:
        pass
    if "--setup" in sys.argv:
        run_setup(full="--all" in sys.argv)
        return
    if "--audiocheck" in sys.argv:
        audiocheck()
        return
    probe = QLocalSocket()
    probe.connectToServer(ipc_name())
    if probe.waitForConnected(300):
        probe.disconnectFromServer()      # Erstinstanz öffnet die Einstellungen
        return
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Quassel")
    app.setWindowIcon(app_icon())
    from ..state import mark_daemon_running
    mark_daemon_running()      # Lebensnachweis für dictate --once und MCP ask_user
    win = WinApp(app)          # Referenz halten! Sonst räumt der GC das
    app.exec()                 # Objekt sofort ab -> Qt-Crash (0xC0000005)
    del win


if __name__ == "__main__":
    main()
