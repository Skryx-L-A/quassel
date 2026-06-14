"""Quassel für Windows — alles in einem Qt-Prozess.

Tray-Icon (an/aus, Einstellungen, Beenden) + schwebende Pille + Hotkey-Hook
+ lokaler whisper-server.exe. Teilt config/i18n/textproc/whisperclient mit
der Linux-Version; nur Tastatur/Audio/Einfügen sind Windows-spezifisch.
"""
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .. import (__version__, aimodes, config, i18n, learn, progmode, stats,
                textproc, textreplace, vad, wakeword, whisperclient)
from ..audio import RATE, SAMPLE_BYTES, wav_from_raw
from ..config import CHORDS
from ..mediacontrol import AudioDucker
from ..streaming import StreamTyper
from ..i18n import tr
from ..state import PARTWAV, WAV
from . import server
from .audio_win import Recorder
from .hook import CHORDS_VK, KeyboardHook
from .machine import ChordMachine
from .paste import paste, send_backspaces, send_enter, type_chunk

PARTIAL_EVERY = 2.0
PARTIAL_WINDOW = 15


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


class PartialWorker(threading.Thread):
    """Live-Vorschau: Teiltranskripte während der Aufnahme."""

    def __init__(self, rec, cfg, on_partial, is_streaming=None):
        super().__init__(daemon=True)
        self.rec, self.cfg = rec, cfg
        self.on_partial = on_partial
        self.is_streaming = is_streaming
        self.stop_event = threading.Event()

    def run(self):
        t = time.monotonic()
        whisperclient.ensure_server()
        dlog("partial: ensure_server %.2fs" % (time.monotonic() - t))
        while not self.stop_event.wait(PARTIAL_EVERY):
            if not self.rec.active:
                return
            # Weder Vorschau noch Streaming -> Teiltranskript spart man sich (CPU).
            if not self.cfg.pill_preview and not (self.is_streaming and self.is_streaming()):
                continue
            data = self.rec.raw_bytes()
            if len(data) < RATE * SAMPLE_BYTES // 2:
                continue
            data = data[-(RATE * SAMPLE_BYTES * PARTIAL_WINDOW):]
            try:
                wav_from_raw(data, PARTWAV)
            except OSError:
                continue
            t = time.monotonic()
            text = whisperclient.transcribe(PARTWAV, self.cfg, timeout=20)
            dlog("partial: transcribe %.2fs (%d Bytes Audio)"
                 % (time.monotonic() - t, len(data)))
            if text is not None and not self.stop_event.is_set() and self.rec.active:
                kind, clean = textproc.postprocess(text, self.cfg)
                if kind == "text":
                    self.on_partial(clean)

    def stop(self):
        self.stop_event.set()


class _Bubble(QWidget):
    """Textblase über der Pille (Live-Vorschau / Ergebnis), wie auf Linux.

    Eigenes Top-Level-Fenster, damit die Pille darunter fest verankert
    bleibt, wenn die Blase wächst oder schrumpft."""

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

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        op = max(0.15, min(1.0, self.cfg.pill_opacity))
        p.setBrush(QColor(16, 16, 22, int(op * 255)))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 12, 12)

    def set_text(self, text, italic=False):
        """Nur Inhalt/Größe; sichtbar schalten übernimmt die Pille."""
        if not text:
            return False
        short = text if len(text) <= 140 else "…" + text[-139:]
        s = max(0.6, min(2.0, self.cfg.pill_scale))
        style = "color: #cfcfd8; font-size: %dpx;" % int(11 * s)
        if italic:
            style += " font-style: italic;"
        self.label.setStyleSheet(style)
        self.label.setText(short)
        self.adjustSize()
        return True


class _PillBody(QWidget):
    """Die eigentliche Pille: minimale Wellenform aus fünf Balken in der
    Direction-B-Palette (Pine; Grau=aus, Bernstein=Fehler). Bewegt sich nur
    bei Aufnahme, sonst ruhend — gleiche Optik wie die Linux-Pille."""

    REST = [0.30, 0.46, 0.38, 0.52, 0.34]

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.mode = "ready"
        self.t0 = time.monotonic()
        self.resize_to_cfg()

    def resize_to_cfg(self):
        s = max(0.6, min(2.0, self.cfg.pill_scale))
        self.setFixedSize(int(48 * s), int(28 * s))

    def _wave_color(self):
        if self.mode == "off":
            return QColor("#6A786F")
        if self.mode == "error":
            return QColor("#E9A93A")
        return QColor("#34C18C")        # ready / recording / transcribing / done

    def paintEvent(self, _ev):
        import math
        s = max(0.6, min(2.0, self.cfg.pill_scale))
        op = max(0.15, min(1.0, self.cfg.pill_opacity))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(17, 32, 26, int(op * 255)))
        p.setPen(QColor(255, 255, 255, 13))
        radius = self.height() / 2 - 1
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)
        # Fünf Balken, mittig — bewegen sich nur bei Aufnahme.
        p.setPen(Qt.NoPen)
        p.setBrush(self._wave_color())
        wave_w, wave_h, n = 22 * s, 14 * s, 5
        gap = wave_w * 0.11
        bw = (wave_w - gap * (n - 1)) / n
        x0 = (self.width() - wave_w) / 2
        cy = self.height() / 2
        animating = self.mode == "recording"
        t = time.monotonic()
        for i in range(n):
            frac = (0.22 + 0.78 * (0.5 + 0.5 * math.sin(t * 7.5 + i * 1.1))) \
                if animating else self.REST[i]
            bh = max(2.0, wave_h * frac)
            bx = x0 + i * (bw + gap)
            p.drawRoundedRect(QRectF(bx, cy - bh / 2, bw, bh), bw / 2, bw / 2)

    def set_mode(self, mode):
        if mode == "recording" and self.mode != "recording":
            self.t0 = time.monotonic()
        self.mode = mode
        self.resize_to_cfg()
        self.update()


class Pill(QWidget):
    """Schwebendes Overlay unten-mittig auf dem Monitor mit dem Mauszeiger.
    Die Pille selbst ist fest verankert; die Textblase ist ein eigenes
    Fenster darüber und darf wachsen, ohne die Pille zu verschieben."""

    def __init__(self):
        super().__init__(None,
                         Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.cfg = config.Cfg()
        self.mode = "ready"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.body = _PillBody(self.cfg)
        lay.addWidget(self.body)
        self.bubble = _Bubble(self.cfg)
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
            self.body.update()
        self.reposition()

    def set_mode(self, mode, text=""):
        self.mode = mode
        self.body.set_mode(mode)
        if mode == "recording":
            has_text = self.bubble.set_text(text, italic=True)  # Live-Vorschau
        elif mode in ("done", "error"):
            has_text = self.bubble.set_text(text)
        else:
            has_text = False
        self.adjustSize()
        self.setVisible(self.cfg.pill_enabled)
        self.bubble.setVisible(has_text and self.cfg.pill_enabled)
        self.reposition()

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
        self.streamer = None
        self._clip_backup = None
        self.ducker = AudioDucker()   # Musik/Ton beim Diktieren leise schalten
        self._capturing = False       # lief rec.start erfolgreich? (sonst Mikro-Fehler)
        self.enabled = True
        self.wake = None              # WakeListener-Thread (nur wenn Wake-Word an)

        self.pill = Pill()
        self.sig_state.connect(self._apply_state)

        ga, gb = CHORDS_VK[self.cfg.chord]
        self.machine = ChordMachine(
            ga, gb, self.on_start, self.on_finish, self.on_cancel,
            self.cfg.hold_min, self.cfg.double_window)
        self.machine.on_handsfree = self.enable_streaming
        self.hook = KeyboardHook()
        self.hook.start()

        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip("Quassel")
        self.build_menu()
        self.tray.show()

        self.poll = QTimer()
        self.poll.timeout.connect(self.pump)
        self.poll.start(20)

        # Hotkey-Wechsel aus den Einstellungen live übernehmen
        self.cfgpoll = QTimer()
        self.cfgpoll.timeout.connect(self.reload_chord)
        self.cfgpoll.start(1000)

        # Einzelinstanz-Kanal: weitere Starts melden sich hier und wir
        # öffnen stattdessen das Einstellungsfenster
        QLocalServer.removeServer("quassel-app")
        self.ipc = QLocalServer()
        self.ipc.newConnection.connect(self.on_second_instance)
        self.ipc.listen("quassel-app")

        if not server.installed():
            threading.Thread(target=self.first_run_setup, daemon=True).start()

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
                self.sig_state.emit("error", tr("no_server"))
        except Exception as e:  # noqa: BLE001
            self.sig_state.emit("error", str(e)[:80])

    # ------------------------------------------------------------------ Menü
    def build_menu(self):
        menu = QMenu()
        self.act_toggle = QAction(tr("turn_off"), menu)
        self.act_toggle.triggered.connect(self.toggle)
        menu.addAction(self.act_toggle)
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
        if not self.enabled and self.rec.active:
            self.on_cancel("canceled_key")
        self.sig_state.emit("ready" if self.enabled else "off", "")

    def quit(self):
        self.hook.stop()
        server.stop()
        self.app.quit()

    # --------------------------------------------------------------- Hotkey
    def reload_chord(self):
        """Geänderten Chord aus der Config übernehmen (nicht mitten in
        einer laufenden Aufnahme — die Maschine würde den Zustand verlieren)."""
        self.cfg.reload()
        ga, gb = CHORDS_VK[self.cfg.chord]
        if (set(ga) != self.machine.a or set(gb) != self.machine.b) \
                and self.machine.state == "idle" and not self.rec.active:
            self.machine = ChordMachine(
                ga, gb, self.on_start, self.on_finish, self.on_cancel,
                self.cfg.hold_min, self.cfg.double_window)
            self.machine.on_handsfree = self.enable_streaming
        self._sync_wakeword()       # Wake-Word nach Konfig starten/stoppen

    def pump(self):
        while not self.hook.events.empty():
            vk, pressed = self.hook.events.get()
            if self.enabled:
                self.machine.key(vk, pressed)
        self.machine.poll()

    def on_start(self):
        dlog("on_start")
        self.cfg.reload()
        t = time.monotonic()
        if not self.rec.start(self.cfg.mic):
            dlog("on_start: rec.start FEHLGESCHLAGEN")
            # Nicht still scheitern lassen: sonst bleibt die Pille grau und der
            # Nutzer weiss nicht, warum nichts passiert (z.B. Mikro aus/weg).
            self.sig_state.emit("error", tr("no_mic"))
            return
        dlog("on_start: rec.start %.2fs" % (time.monotonic() - t))
        self.ducker.apply(self.cfg.mute_mode)   # Musik pausieren / Ton stumm
        self._capturing = True
        self.streamer = None
        self._clip_backup = None
        self.partial = PartialWorker(self.rec, self.cfg, self._on_partial,
                                     is_streaming=lambda: self.streamer is not None)
        self.partial.start()
        self.sig_state.emit("recording", "")

    def _on_partial(self, text):
        """Teiltranskript (nachbearbeitet): live tippen + Pille aktualisieren."""
        if self.streamer is not None:
            self.streamer.update(text)
            rest = text[len(self.streamer.typed):].strip() if self.cfg.pill_preview else ""
            self.sig_state.emit("recording", rest)
        else:
            self.sig_state.emit("recording", text if self.cfg.pill_preview else "")

    def enable_streaming(self):
        """Freihand-Modus erkannt -> Streaming starten, falls eingeschaltet."""
        if self.streamer is not None or not self.cfg.streaming:
            return
        from .paste import streaming_begin
        self._clip_backup = streaming_begin()
        self.streamer = StreamTyper(self.cfg.streaming_mode, type_chunk, send_backspaces)

    def on_cancel(self, reason_key):
        self._capturing = False
        if self.partial:
            self.partial.stop()
            self.partial = None
        if self.streamer is not None:
            from .paste import streaming_restore
            streaming_restore(self._clip_backup)
            self.streamer = None
        self.rec.stop()
        self.ducker.restore()                   # Musik/Ton wiederherstellen
        self.sig_state.emit("ready", "")

    def on_finish(self):
        dlog("on_finish")
        if not self._capturing:
            return        # nichts aufgenommen (z.B. Mikro-Fehler): Meldung stehen lassen
        self._capturing = False
        if self.partial:
            self.partial.stop()
            self.partial = None
        t = time.monotonic()
        self.rec.stop()
        self.ducker.restore()                   # Musik/Ton wiederherstellen
        dlog("on_finish: rec.stop %.2fs" % (time.monotonic() - t))
        data = self.rec.raw_bytes()
        if len(data) < 8000:
            self.sig_state.emit("ready", "")
            return
        self.sig_state.emit("transcribing", "")
        threading.Thread(target=self._transcribe, args=(data,), daemon=True).start()

    def _transcribe(self, data):
        # Fehler hier liefen früher ins Leere: der Daemon-Thread starb leise
        # und die Pille blieb für immer auf "Transkribiere..."
        try:
            self._transcribe_inner(data)
        except Exception as e:  # noqa: BLE001
            import traceback
            dlog("transcribe: EXCEPTION " + traceback.format_exc())
            self.sig_state.emit("error", str(e)[:80])

    def _transcribe_inner(self, data):
        t = time.monotonic()
        if not whisperclient.ensure_server():
            self.sig_state.emit("error", tr("no_server"))
            return
        dlog("transcribe: ensure_server %.2fs" % (time.monotonic() - t))
        wav_from_raw(data, WAV)
        t = time.monotonic()
        raw = whisperclient.transcribe(WAV, self.cfg)
        dlog("transcribe: inference %.2fs (%d Bytes Audio)"
             % (time.monotonic() - t, len(data)))
        if raw is None:
            self.sig_state.emit("error", tr("no_server"))
            return
        from .paste import streaming_restore
        kind, value = textproc.postprocess(raw, self.cfg)
        if kind is None:
            if self.streamer is not None:
                streaming_restore(self._clip_backup)
                self.streamer = None
            self.sig_state.emit("error", tr("nothing"))
            return
        if kind == "command":
            action = value
            live_typed = len(self.streamer.typed) if self.streamer is not None else 0
            if self.streamer is not None:
                streaming_restore(self._clip_backup)
                self.streamer = None
            if action == "enter":
                if live_typed:
                    send_backspaces(live_typed)
                send_enter()
                self.last_paste_len = 0
                self.sig_state.emit("done", tr("pressed_enter"))
                return
            undo = live_typed if live_typed else self.last_paste_len
            if undo:
                send_backspaces(undo)
                self.last_paste_len = 0
                self.sig_state.emit("done", tr("deleted"))
            else:
                self.sig_state.emit("error", tr("nothing"))
            return
        mech = self._refine_mechanical(value)
        t = time.monotonic()
        if self.streamer is not None:
            final = self._ai_refine(mech) if self.cfg.ai_enabled else mech
            typed = self.streamer.finish(final)
            self.last_paste_len = len(typed)
            streaming_restore(self._clip_backup)
            self.streamer = None
            value = final
            dlog("transcribe: streaming finish %.2fs" % (time.monotonic() - t))
        elif self.cfg.ai_enabled:
            # KI braucht Sekunden -> ERST Rohtext einfügen (Fokus noch sicher),
            # DANN durch die KI-Fassung ersetzen.
            paste(mech)
            self.last_paste_len = len(mech)
            final = self._ai_refine(mech)
            if final != mech:
                send_backspaces(len(mech))
                paste(final)
                self.last_paste_len = len(final)
                dlog("transcribe: ai replace %d -> %d" % (len(mech), len(final)))
            value = final
        else:
            paste(mech)
            self.last_paste_len = len(mech)
            value = mech
            dlog("transcribe: paste %.2fs (%d Zeichen)"
                 % (time.monotonic() - t, len(value)))
        self.sig_state.emit("done", value)
        secs = len(data) / (RATE * SAMPLE_BYTES)
        self._after_insert(value, secs)

    def _refine_mechanical(self, text):
        """Schnelle lokale Umformungen ohne Netz/KI: Programmier-Diktat + Textersetzungen."""
        if self.cfg.programmer_mode:
            text = progmode.apply(text)
        if self.cfg.text_replace:
            rules = config.replacement_rules()
            if rules:
                text = textreplace.apply_rules(text, rules)
        return text

    def _refine(self, text):
        """Mechanische Umformungen + (optional) lokale KI — synchron (Wake-Pfad)."""
        text = self._refine_mechanical(text)
        if self.cfg.ai_enabled:
            text = self._ai_refine(text)
        return text

    def _ai_refine(self, text):
        """Lokale KI-Nachbearbeitung (Ollama): Sprach-Modus hat Vorrang, sonst
        optionaler Auto-Modus. Fail-soft: bei Fehler bleibt der Rohtext."""
        self.cfg.ai_modes_text = config.ai_modes_text()
        mode, remaining = None, text
        if self.cfg.ai_voice_modes:
            m, rest = aimodes.detect_voice_mode(text)
            if m is None:
                m, rest = self._detect_custom_voice(text)
            if m:
                mode, remaining = m, rest
        if mode is None and self.cfg.ai_post_process:
            mode = self.cfg.ai_post_mode
        if mode is None or not remaining.strip():
            return text
        return aimodes.transform(remaining, mode, self.cfg)

    def _detect_custom_voice(self, text):
        custom = aimodes.parse_custom(getattr(self.cfg, "ai_modes_text", "") or "")
        low = text.lower().strip()
        for name in sorted(custom, key=len, reverse=True):
            if low.startswith(name):
                rest = text.strip()[len(name):].lstrip(" ,:").strip()
                return name, rest
        return None, text

    def _after_insert(self, value, seconds=0.0):
        """Nach dem Einfügen: Verlauf, Statistik, Wörterbuch-Lernen."""
        if self.cfg.history_enabled:
            try:
                config.history_append(value)
            except OSError:
                pass
        if self.cfg.stats_enabled:
            try:
                stats.record(value, seconds=seconds)
            except Exception:    # noqa: BLE001
                pass
        if self.cfg.auto_learn:
            try:
                merged, added = learn.learn(value, config.dictionary_words())
                if added:
                    config.dictionary_save("\n".join(merged))
            except Exception:    # noqa: BLE001
                pass

    # ----------------------------------------------------- Wake-Word (opt-in)
    def _sync_wakeword(self):
        """Listener nach Konfig starten/stoppen (idempotent)."""
        if self.cfg.wakeword_enabled and self.wake is None:
            self.wake = wakeword.WakeListener(
                self.cfg, self._wake_record_utterance, self._wake_transcribe,
                self._wake_insert, is_busy=lambda: self.rec.active or not self.enabled)
            self.wake.start()
        elif not self.cfg.wakeword_enabled and self.wake is not None:
            self.wake.stop()
            self.wake = None

    def _wake_record_utterance(self):
        rec = Recorder()
        if not rec.start(self.cfg.mic):
            return None
        det = vad.SilenceDetector()
        prev = 0
        deadline = time.monotonic() + 12
        try:
            while time.monotonic() < deadline and self.wake is not None:
                time.sleep(0.1)
                data = rec.raw_bytes()
                new = data[prev:]
                prev = len(data)
                if new:
                    det.feed(new)
                if det.stopped:
                    break
        finally:
            rec.stop()
        data = rec.raw_bytes()
        if not det.speech_started or len(data) < 8000:
            return None
        return data

    def _wake_transcribe(self, pcm):
        if not pcm or not whisperclient.ensure_server():
            return None
        try:
            wav_from_raw(pcm, WAV)
        except OSError:
            return None
        return whisperclient.transcribe(WAV, self.cfg, timeout=30)

    def _wake_insert(self, raw_text):
        kind, value = textproc.postprocess(raw_text, self.cfg)
        if kind != "text" or not value.strip():
            return
        value = self._refine(value)
        paste(value)
        self.last_paste_len = len(value)
        self.sig_state.emit("done", value)
        self._after_insert(value)

    def _apply_state(self, state, text):
        self.pill.cfg.reload()
        self.pill.set_mode(state, text)
        if state in ("done", "error"):
            QTimer.singleShot(3000, lambda: self.pill.set_mode(
                "ready" if self.enabled else "off"))


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
    probe.connectToServer("quassel-app")
    if probe.waitForConnected(300):
        probe.disconnectFromServer()      # Erstinstanz öffnet die Einstellungen
        return
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Quassel")
    app.setWindowIcon(app_icon())
    win = WinApp(app)          # Referenz halten! Sonst räumt der GC das
    app.exec()                 # Objekt sofort ab -> Qt-Crash (0xC0000005)
    del win


if __name__ == "__main__":
    main()
