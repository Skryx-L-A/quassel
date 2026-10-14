"""Quassel-Kontrollzentrum (Qt/PySide6) — klassisches Einstellungsfenster mit
Seitenleisten-Sektionen (Allgemein, Spracherkennung, Wörterbuch, Verlauf,
System), bewusst einfach und erweiterbar (neue Seite = neuer Eintrag in der
build()-Liste + eigene page_*-Methode).

Nur eine Fernbedienung: Fenster schließen beendet Quassel nicht.
Einstellungen wirken sofort (der Daemon liest die Config live).
"""
import os
import plistlib
import posixpath
import subprocess
import sys
import threading
import time
import wave

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QSpinBox,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from . import (__version__, ai, aimodes, beep, cleanup, config, diagnostics, dictprompt, fsutil,
               i18n, profile, profiles, stats, transcribe_file, updatecheck, vad, wakeword,
               whisperclient)
from .dictation import failure_text, mac_mic_permission
from .audio import RATE, mac_backend
from .config import MODEL_URL, MODELS
from .i18n import tr
from .net import download as net_download
from .pill_qt import app_icon
from .state import RUNDIR, LevelMeter
if os.name == "nt":
    from .win.audio_win import list_mics
    from .win.paste import clip_copy
elif sys.platform == "darwin":
    from .audio import list_mics, record_command
    from .platform_mac import clip_copy
else:
    from .audio import list_mics, record_command
    from .platform_linux import clip_copy

FILE_EXT = {"text": ".txt", "srt": ".srt", "vtt": ".vtt"}

# Stylesheet-Vorlage. Die Markenfarbe (Pine-Grün, Direction B "Lokal") wird je
# nach hellem/dunklem System-Theme eingesetzt — der Rest bleibt bewusst
# nativ/palette-basiert, damit das Fenster sich dem Desktop anpasst.
STYLE = """
QGroupBox {{
    font-weight: 600;
    border: 1px solid palette(midlight);
    border-radius: 10px;
    margin-top: 16px;
    padding: 14px 14px 12px 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: {accent};
    font-weight: 700;
}}
QWidget#brandhead {{
    background: palette(alternate-base);
    border-bottom: 1px solid palette(midlight);
}}
QLabel#brandword {{
    font-size: 15px;
    font-weight: 800;
}}
QListWidget#sidebar {{
    border: none;
    background: palette(alternate-base);
    font-size: 13px;
    outline: none;
    padding-top: 8px;
}}
QListWidget#sidebar::item {{
    padding: 9px 14px;
    border-radius: 8px;
    margin: 2px 8px;
}}
QListWidget#sidebar::item:hover {{
    background: {accent_soft};
}}
QListWidget#sidebar::item:selected {{
    background: {accent};
    color: {accent_text};
}}
QListWidget#sidebar::item:selected:focus {{
    border: 2px solid {focus};
}}
QLabel#desc {{ color: {muted}; }}
"""

# Mindestkontrast für Text (WCAG 2.1 AA, normaler Text).
MIN_CONTRAST = 4.5


def _luminance(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.red()) + 0.7152 * lin(c.green()) + 0.0722 * lin(c.blue())


def contrast_ratio(a, b):
    la, lb = sorted((_luminance(QColor(a)), _luminance(QColor(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def readable(fg, bg, min_ratio=MIN_CONTRAST):
    """fg so weit Richtung Schwarz bzw. Weiß schieben (je nachdem, was auf bg
    mehr Kontrast gibt), bis der Mindestkontrast erreicht ist. Hält Farbton
    und Zurückhaltung, wo es geht, statt pauschal auf Schwarz zu gehen."""
    end = QColor("#000000") if _luminance(QColor(bg)) > 0.18 else QColor("#FFFFFF")
    for i in range(21):
        c = _mix(fg, end, i / 20)
        if contrast_ratio(c, bg) >= min_ratio:
            return c
    return end


IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
# Schieberegler (Tastatur) und das KI-Modellfeld speichern erst nach dieser
# Pause statt bei jedem Schritt bzw. Tastendruck (H121); Ziehen mit der Maus
# speichert beim Loslassen.
SAVE_DELAY_MS = 400


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

LAUNCH_AGENT_LABEL = "de.skryx.quassel"
LAUNCH_AGENT_PLIST = os.path.expanduser(
    "~/Library/LaunchAgents/" + LAUNCH_AGENT_LABEL + ".plist")


def sysctl(*args):
    if IS_WINDOWS or IS_MAC:
        class _R:  # kein systemd auf Windows/macOS; Tray-/Menüleisten-App steuert alles
            returncode = 1
        return _R()
    return subprocess.run(["systemctl", "--user", *args], check=False,
                          capture_output=True)


def open_text_file(path):
    """Textdatei im Editor öffnen; True, wenn das System sie übernommen hat.
    macOS: `open -t` (Standard-Texteditor) — .ini ist dort oft keiner App
    zugeordnet, Qt meldete dann Erfolg, und nichts ging auf. Windows: Notepad,
    falls keine App für .ini eingetragen ist. Linux: xdg-open/Portal über Qt."""
    if IS_MAC:
        return subprocess.run(["open", "-t", path], check=False,
                              capture_output=True).returncode == 0
    if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
        return True
    if IS_WINDOWS:
        try:
            subprocess.Popen(["notepad.exe", path])
        except OSError:
            return False
        return True
    return False


def daemon_unit():
    """Diktat-Dienst dieser Instanz: quasseld.service, mit QUASSEL_PROFILE die
    Vorlage quasseld@<profil>.service (systemd/quasseld@.service)."""
    return profile.service("quasseld")


def service_units(start):
    """systemd-Units für An/Aus dieser Instanz. Ein Profil schaltet nur seine
    eigenen (Dienst, Server und beim Start die eigene Pille); die geteilten
    Dienste (ydotoold) und die Pille der installierten App bleiben unberührt."""
    own = [daemon_unit(), profile.service()]
    if profile.name():
        return own + ([profile.service("quassel-pill")] if start else [])
    return own + (["quassel-pill"] if start else ["quassel-ydotoold"])


def daemon_active():
    if IS_WINDOWS or IS_MAC:
        # Windows: Fenster läuft im Tray-App-Prozess selbst. macOS: das
        # Zentrum wird von der laufenden Menüleisten-App geöffnet.
        return True
    return sysctl("is-active", "--quiet", daemon_unit()).returncode == 0


def profile_autostart_blocked():
    """These platforms have only the installed app's autostart identity."""
    return bool(os.environ.get("QUASSEL_PROFILE")) and (IS_WINDOWS or IS_MAC)


def autostart_enabled():
    if profile_autostart_blocked():
        return False
    if IS_WINDOWS:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "Quassel")
            return True
        except OSError:
            return False
    if IS_MAC:
        return os.path.exists(LAUNCH_AGENT_PLIST)
    return sysctl("is-enabled", "--quiet", daemon_unit()).returncode == 0


def mac_app_path():
    """Pfad der laufenden .app (gefroren liegt sys.executable in
    Contents/MacOS); None außerhalb eines Bundles."""
    if not getattr(sys, "frozen", False):
        return None
    # Ein Bundle ist ein POSIX-Pfad, auch wenn ein Test das auf Windows prüft.
    app = posixpath.dirname(posixpath.dirname(posixpath.dirname(
        posixpath.abspath(sys.executable))))
    return app if app.endswith(".app") else None


def mac_autostart_set(on):
    """macOS: LaunchAgent unter ~/Library/LaunchAgents schreiben/entfernen
    und sofort per launchctl bootstrap/bootout an-/abmelden. Gestartet wird
    über 'open' — das aktiviert eine schon laufende Instanz statt eine
    zweite zu spawnen."""
    if profile_autostart_blocked():
        raise ValueError("profile autostart is unavailable on macOS")
    uid = os.getuid()
    if on:
        app = mac_app_path()
        args = (["/usr/bin/open", app] if app
                else ["/usr/bin/open", "-a", "Quassel"])
        plist = {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": args,
            "RunAtLoad": True,
        }
        os.makedirs(os.path.dirname(LAUNCH_AGENT_PLIST), exist_ok=True)
        with open(LAUNCH_AGENT_PLIST, "wb") as f:
            plistlib.dump(plist, f)
        # Erst abmelden: bootstrap schlägt still fehl, wenn der Agent schon
        # geladen ist (doppeltes Aktivieren) — bootout macht es idempotent.
        subprocess.run(["launchctl", "bootout",
                        f"gui/{uid}/{LAUNCH_AGENT_LABEL}"],
                       check=False, capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}",
                        LAUNCH_AGENT_PLIST], check=False, capture_output=True)
    else:
        subprocess.run(["launchctl", "bootout",
                        f"gui/{uid}/{LAUNCH_AGENT_LABEL}"],
                       check=False, capture_output=True)
        try:
            os.remove(LAUNCH_AGENT_PLIST)
        except FileNotFoundError:
            pass


def autostart_set(on):
    """Windows: Run-Key in der Registry (gleicher Wert wie im Inno-Setup)."""
    if profile_autostart_blocked():
        raise ValueError("profile autostart is unavailable on Windows")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Quassel", 0, winreg.REG_SZ,
                              '"%s"' % sys.executable)
        else:
            try:
                winreg.DeleteValue(k, "Quassel")
            except OSError:
                pass


def restart_server():
    """Nach Modellwechsel: Server mit neuem Modell neu starten."""
    if IS_WINDOWS:
        from .win import server
        server.stop()
        server.start()
    elif IS_MAC:
        # Server läuft im Menüleisten-App-Prozess: über kill_orphans (findet
        # ihn per Binary+Port prozessübergreifend) beenden, kurz auf den
        # freien Port warten, dann losgelöst neu starten.
        from . import server_mac
        server_mac.kill_orphans()
        for _ in range(20):
            if not server_mac.port_in_use():
                break
            time.sleep(0.15)
        else:
            print("restart_server: Port nach 3 s noch belegt — "
                  "alter Server läuft weiter, kein Neustart", file=sys.stderr)
        server_mac.start()
    else:
        sysctl("try-restart", profile.service())


class MicTestError(Exception):
    """Mikrofontest konnte nicht aufnehmen; args[0] ist der i18n-Schlüssel."""


class NoWheel(QObject):
    """Verhindert, dass das Mausrad beim Scrollen der Seite ungewollt
    Dropdowns/Slider verstellt — nur fokussierte Elemente reagieren."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Wheel and not obj.hasFocus():
            ev.ignore()
            return True
        return False


class Bridge(QObject):
    """Thread → GUI-Signale."""
    progress = Signal(float)
    message = Signal(str)
    model_done = Signal(str)
    file_done = Signal(str, str)      # (Statuszeile, Ergebnistext)
    file_progress = Signal(float, float)  # (erledigt, gesamt) in Sekunden Audio
    update_done = Signal(dict)        # updatecheck.check()-Resultat
    ai_models = Signal(dict)          # {"available":bool,"models":[...]}
    ai_test_done = Signal(str)        # Ergebnis des KI-Tests
    diag_done = Signal(str, str)      # (Ziel "copy"/"save", Bericht)


class Center(QMainWindow):
    def __init__(self, controller=None):
        """controller: unter Windows die Tray-App (WinApp), unter macOS die
        Menüleisten-App (MacApp) im selben Prozess — An/Aus läuft dann über
        sie statt über systemd."""
        super().__init__()
        self.controller = controller
        self.cfg = config.Cfg()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        self.bridge = Bridge()
        self.bridge.progress.connect(self.on_progress)
        self.bridge.message.connect(self.on_message)
        self.bridge.model_done.connect(self.on_model_done)
        self.bridge.file_done.connect(self.on_file_done)
        self.bridge.file_progress.connect(self.on_file_progress)
        self.bridge.update_done.connect(self.on_update_done)
        self.bridge.ai_models.connect(self.on_ai_models)
        self.bridge.ai_test_done.connect(self.on_ai_test_done)
        self.bridge.diag_done.connect(self.on_diag_done)
        self.nowheel = NoWheel(self)
        # Ein bestätigter fremder KI-Endpunkt wird nicht bei jedem weiteren
        # Speichern erneut abgefragt; der gespeicherte gilt als bestätigt.
        self._ai_endpoint_ok = self.cfg.ai_endpoint
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(SAVE_DELAY_MS)
        self.save_timer.timeout.connect(self.save_settings)
        self._loading = True
        self.build()
        self._loading = False
        self.refresh_status()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh_status)
        timer.start(2000)
        QTimer.singleShot(0, self._onboard_if_shown)
        if self.cfg.update_check:
            QTimer.singleShot(1500, lambda: updatecheck.check_async(
                __version__, self.bridge.update_done.emit))

    # ------------------------------------------------------------- Hilfen
    def guard(self, w):
        """Mausrad-Schutz + sauberer Fokus für Dropdowns/Slider."""
        w.setFocusPolicy(Qt.StrongFocus)
        w.installEventFilter(self.nowheel)
        if isinstance(w, QComboBox):
            # Lange Einträge dürfen die Seite nicht breiter als das Fenster
            # machen (sonst waagerechter Rollbalken); Text wird gekürzt.
            w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            w.setMinimumContentsLength(12)
        return w

    def group(self, title, parent_layout):
        box = QGroupBox(title)
        form = QVBoxLayout(box)
        form.setSpacing(8)
        parent_layout.addWidget(box)
        return form

    def desc(self, text, layout):
        lbl = QLabel(text)
        lbl.setObjectName("desc")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)
        return lbl

    def labeled_row(self, label, widget, layout):
        """Beschriftung und Feld verknüpfen: Bildschirmleser nennen den Text,
        ein Klick auf die Beschriftung setzt den Fokus ins Feld."""
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setBuddy(widget)
        widget.setAccessibleName(label)
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        layout.addLayout(row)
        return lbl

    def save_soon(self, *_a):
        """Speichern nach kurzer Pause — gedrückt gehaltene Pfeiltasten oder
        Tippen schreiben config.ini dann einmal statt dutzendfach."""
        if not self._loading:
            self.save_timer.start()

    def _save_on_release(self, slider):
        """Ziehen: erst beim Loslassen speichern; Tastatur: nach der Pause."""
        slider.valueChanged.connect(
            lambda *_a: None if slider.isSliderDown() else self.save_soon())
        slider.sliderReleased.connect(self.save_settings)

    def closeEvent(self, ev):
        if self.save_timer.isActive():      # nichts Ungespeichertes verlieren
            self.save_timer.stop()
            self.save_settings()
        super().closeEvent(ev)

    def select_data(self, combo, value, default=0):
        """Combo-Eintrag mit passenden userData auswählen (Reihenfolge-unabhängig)."""
        i = combo.findData(value)
        combo.setCurrentIndex(i if i >= 0 else default)

    def page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(4)
        scroll.setWidget(inner)
        return scroll, lay

    # ------------------------------------------------------------- Aufbau
    def build(self):
        self.setWindowTitle(tr("app_name"))
        self.setWindowIcon(app_icon())
        self.setMinimumSize(680, 520)

        # Markenakzent (Pine) themenabhängig wählen, damit der Kontrast in hellen
        # wie dunklen Desktop-Themes stimmt. Auf der Website dieselbe Identität.
        dark = self.palette().color(QPalette.Window).value() < 128
        if dark:
            self._accent = "#34C18C"
            self._accent_text = "#06140E"
            self._accent_hover = "#48D49E"
            accent_soft = "rgba(52,193,140,0.16)"
        else:
            self._accent = "#0F6B4F"
            self._accent_text = "#FFFFFF"
            self._accent_hover = "#0B5238"
            accent_soft = "rgba(15,107,79,0.12)"
        # Beschreibungstexte und die Beta-Warnung (Bernstein #E9A93A) erreichen
        # auf hellem Grund nur ~2:1; beide werden je Thema auf >= 4,5:1 gebracht.
        window = self.palette().color(QPalette.Window)
        text = self.palette().color(QPalette.WindowText)
        self._muted = readable(_mix(text, window, 0.4), window).name()
        self._warn = readable(QColor("#E9A93A"), window).name()
        self._focus = text.name()
        self.setStyleSheet(STYLE.format(
            accent=self._accent, accent_text=self._accent_text,
            accent_soft=accent_soft, muted=self._muted, focus=self._focus))

        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Linke Spalte: Marken-Kopf (Logo + Wortmarke) über der Seitenleiste.
        left = QWidget()
        left.setFixedWidth(176)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        head = QWidget()
        head.setObjectName("brandhead")
        hh = QHBoxLayout(head)
        hh.setContentsMargins(16, 12, 14, 12)
        hh.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(22, 22))
        hh.addWidget(logo)
        word = QLabel("Quassel")
        word.setObjectName("brandword")
        hh.addWidget(word)
        hh.addStretch(1)
        lv.addWidget(head)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setAccessibleName(tr("nav_sections"))
        self.stack = QStackedWidget()
        lv.addWidget(self.sidebar, 1)
        outer.addWidget(left)
        outer.addWidget(self.stack, 1)

        self._page_keys = []
        # Kurze Seiten (G111): eine Aufgabe je Seite, Beta-Funktionen gesammelt
        # unten vor „System".
        for key, builder in [("nav_general", self.page_general),
                             ("nav_pill", self.page_pill),
                             ("nav_dictating", self.page_dictating),
                             ("nav_speech", self.page_speech),
                             ("nav_mic", self.page_mic),
                             ("nav_dict", self.page_dict),
                             ("nav_replace", self.page_replace),
                             ("nav_file", self.page_file),
                             ("nav_ai", self.page_ai),
                             ("nav_history", self.page_history),
                             ("nav_stats", self.page_stats),
                             ("nav_beta", self.page_beta),
                             ("nav_system", self.page_system),
                             ("about", self.page_about)]:
            QListWidgetItem(tr(key), self.sidebar)
            self.stack.addWidget(builder())
            self._page_keys.append(key)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self.setCentralWidget(root)

    def show_page(self, key):
        """Seite über ihren Navigationsschlüssel wählen (nicht über die
        Position — die ändert sich, wenn Seiten dazukommen)."""
        if key in self._page_keys:
            self.sidebar.setCurrentRow(self._page_keys.index(key))

    # --------------------------------------------------- Seite: Allgemein
    def page_general(self):
        scroll, lay = self.page()

        g = self.group(tr("sec_status"), lay)
        row = QHBoxLayout()
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(13, 13)
        row.addWidget(self.status_dot)
        self.status_lbl = QLabel()
        self.status_lbl.setStyleSheet("font-size: 15px; font-weight: 600;")
        row.addWidget(self.status_lbl)
        row.addStretch(1)
        self.toggle_btn = QPushButton()
        self.toggle_btn.setMinimumHeight(34)
        self.toggle_btn.setStyleSheet(
            f"QPushButton{{background:{self._accent};color:{self._accent_text};"
            f"font-weight:600;padding:7px 26px;border:none;border-radius:9px;}}"
            f"QPushButton:hover{{background:{self._accent_hover};}}"
            f"QPushButton:focus{{border:2px solid {self._focus};}}")
        self.toggle_btn.clicked.connect(self.on_toggle)
        if (IS_WINDOWS or IS_MAC) and self.controller is None:
            self.toggle_btn.hide()      # ohne Tray-/Menüleisten-App nichts zu schalten
        row.addWidget(self.toggle_btn)
        g.addLayout(row)
        self.hint = self.desc(tr("hint", chord=self.chord_label()), g)

        g = self.group(tr("sec_hotkey"), lay)
        self.chord = self.guard(QComboBox())
        for key in config.CHORDS:
            self.chord.addItem(tr(config.CHORD_LABEL_KEYS[key]), key)
        self.chord.setCurrentIndex(list(config.CHORDS).index(self.cfg.chord))
        self.chord.currentIndexChanged.connect(self.save_settings)
        self.chord.setAccessibleName(tr("sec_hotkey"))
        g.addWidget(self.chord)

        lay.addStretch(1)
        note = QLabel(tr("close_note"))
        note.setObjectName("desc")
        note.setAlignment(Qt.AlignCenter)
        lay.addWidget(note)
        return scroll

    # ----------------------------------------------------- Seite: Pille
    def page_pill(self):
        scroll, lay = self.page()

        g = self.group(tr("sec_pill"), lay)
        self.pill_show = QCheckBox(tr("pill_show"))
        self.pill_show.setChecked(self.cfg.pill_enabled)
        self.pill_show.toggled.connect(self.save_settings)
        g.addWidget(self.pill_show)
        self.pill_size = self.guard(QSlider(Qt.Horizontal))
        self.pill_size.setRange(60, 200)
        self.pill_size.setValue(int(self.cfg.pill_scale * 100))
        self._save_on_release(self.pill_size)
        self.labeled_row(tr("pill_size"), self.pill_size, g)
        self.pill_opacity = self.guard(QSlider(Qt.Horizontal))
        self.pill_opacity.setRange(15, 100)
        self.pill_opacity.setValue(int(self.cfg.pill_opacity * 100))
        self._save_on_release(self.pill_opacity)
        self.labeled_row(tr("pill_opacity"), self.pill_opacity, g)
        self.pill_movable = QCheckBox(tr("pill_movable"))
        self.pill_movable.setChecked(self.cfg.pill_movable)
        self.pill_movable.toggled.connect(self.save_settings)
        row = QHBoxLayout()
        row.addWidget(self.pill_movable)
        row.addStretch(1)
        self.pill_reset_pos = QPushButton(tr("pill_reset_pos"))
        self.pill_reset_pos.clicked.connect(self.reset_pill_position)
        row.addWidget(self.pill_reset_pos)
        g.addLayout(row)
        self.pill_movable_note = None
        if not (IS_WINDOWS or IS_MAC):
            # Die GTK-Pille (Layer-Shell-Overlay) kann nicht frei stehen (E81)
            self.pill_movable_note = self.desc(tr("pill_movable_linux"), g)
        self.pill_click_center = QCheckBox(tr("pill_click_center"))
        self.pill_click_center.setChecked(self.cfg.pill_click_opens_center)
        self.pill_click_center.toggled.connect(self.save_settings)
        g.addWidget(self.pill_click_center)
        self.pill_preview = QCheckBox(tr("pill_preview"))
        self.pill_preview.setChecked(self.cfg.pill_preview)
        self.pill_preview.toggled.connect(self.save_settings)
        g.addWidget(self.pill_preview)

        lay.addStretch(1)
        return scroll

    # --------------------------------------------- Seite: Beim Diktieren
    def page_dictating(self):
        """Ton, Medien und Live-Tippen: was während eines Diktats passiert."""
        scroll, lay = self.page()

        g = self.group(tr("sec_dictating"), lay)
        self.mute_combo = self.guard(QComboBox())
        for val, key in (("off", "mute_off"), ("music", "mute_music"), ("all", "mute_all")):
            self.mute_combo.addItem(tr(key), val)
        self.mute_combo.setCurrentIndex({"off": 0, "music": 1, "all": 2}.get(self.cfg.mute_mode, 0))
        self.mute_combo.currentIndexChanged.connect(self.save_settings)
        self.labeled_row(tr("mute_label"), self.mute_combo, g)
        self.desc(tr("mute_hint"), g)
        self.beep_chk = QCheckBox(tr("beep_enable"))
        self.beep_chk.setChecked(self.cfg.beep)
        self.beep_chk.toggled.connect(self.save_settings)
        g.addWidget(self.beep_chk)
        self.desc(tr("beep_hint"), g)
        # Geraetewahl fuer die Signaltoene: nur auf macOS, weil nur der dortige
        # Abspielweg ein Ausgabegeraet entgegennimmt (Linux/Windows spielen
        # ueber den jeweiligen System-Player ohne Geraeteauswahl).
        self.beep_out = None
        if IS_MAC:
            self.beep_out = self.guard(QComboBox())
            self.beep_out.addItem(tr("beep_out_system"), "system")
            for name, desc in beep.list_outputs():
                self.beep_out.addItem(desc, name)
                if name == self.cfg.beep_output:
                    self.beep_out.setCurrentIndex(self.beep_out.count() - 1)
            if (self.cfg.beep_output != "system"
                    and self.beep_out.findData(self.cfg.beep_output) < 0):
                # Gespeichertes Geraet ist gerade nicht angeschlossen: trotzdem
                # aufnehmen und auswaehlen, sonst wirft das Oeffnen des Fensters
                # die Einstellung stillschweigend auf "system" zurueck.
                self.beep_out.addItem(self.cfg.beep_output, self.cfg.beep_output)
                self.beep_out.setCurrentIndex(self.beep_out.count() - 1)
            self.beep_out.currentIndexChanged.connect(self.save_settings)
            self.labeled_row(tr("beep_out_label"), self.beep_out, g)
            self.desc(tr("beep_out_hint"), g)

        # --- Streaming-Tippen (Live-Tippen ins Zielfenster, nur Freihand) ---
        g = self.group(tr("sec_streaming"), lay)
        self.stream_on = QCheckBox(tr("streaming_enable"))
        self.stream_on.setChecked(self.cfg.streaming)
        self.stream_on.toggled.connect(self.save_settings)
        self.stream_on.toggled.connect(self._sync_streaming_enabled)
        g.addWidget(self.stream_on)
        self.desc(tr("streaming_hint"), g)
        self.stream_mode = self.guard(QComboBox())
        for val, key in (("word", "streaming_word"), ("stable", "streaming_stable")):
            self.stream_mode.addItem(tr(key), val)
        self.select_data(self.stream_mode, self.cfg.streaming_mode)
        self.stream_mode.currentIndexChanged.connect(self.save_settings)
        self.labeled_row(tr("streaming_mode"), self.stream_mode, g)
        self._sync_streaming_enabled(self.cfg.streaming)


        # --- Per-App-Profile (eigene Datei, profiles.py) ---
        g = self.group(tr("sec_profiles"), lay)
        self.desc(tr("profiles_hint"), g)
        prow = QHBoxLayout()
        self.profiles_btn = QPushButton(tr("profiles_open"))
        self.profiles_btn.clicked.connect(self.on_profiles_open)
        prow.addWidget(self.profiles_btn)
        prow.addStretch(1)
        g.addLayout(prow)
        self.profiles_status = QLabel()
        self.profiles_status.setObjectName("desc")
        self.profiles_status.setWordWrap(True)
        self.profiles_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.profiles_status.setText(tr("profiles_path", path=profiles.PROFILES))
        g.addWidget(self.profiles_status)

        lay.addStretch(1)
        return scroll

    # ----------------------------------------------------- Seite: Sprache
    def page_speech(self):
        scroll, lay = self.page()

        g = self.group(tr("sec_speech"), lay)
        self.lang = self.guard(QComboBox())
        for val, key in (("auto", "lang_auto"), ("de", "lang_de"),
                         ("en", "lang_en"), ("mixed", "lang_mixed")):
            self.lang.addItem(tr(key), val)
        self.select_data(self.lang, self.cfg.language)
        self.lang.currentIndexChanged.connect(self.save_settings)
        self.labeled_row(tr("language"), self.lang, g)
        self.punct = QCheckBox(tr("punctuation"))
        self.punct.setChecked(self.cfg.punctuation)
        self.punct.toggled.connect(self.save_settings)
        g.addWidget(self.punct)
        self.cmds = QCheckBox(tr("commands"))
        self.cmds.setChecked(self.cfg.commands)
        self.cmds.toggled.connect(self.save_settings)
        g.addWidget(self.cmds)
        self.progmode = QCheckBox(tr("programmer_enable"))
        self.progmode.setChecked(self.cfg.programmer_mode)
        self.progmode.toggled.connect(self.save_settings)
        g.addWidget(self.progmode)
        self.desc(tr("programmer_hint"), g)

        g = self.group(tr("model"), lay)
        self.model = self.guard(QComboBox())
        env = config.read_serverenv()
        name = os.path.basename(env.get("MODEL_PATH", ""))
        current = next((m for m in MODELS if name == f"ggml-{m}.bin"),
                       env["MODEL_PATH"] if name else "small")
        if current not in MODELS:
            # Selbst eingetragenes Modell: mit Dateinamen zeigen statt als
            # "small" -- sonst lädt die nächste Auswahl ungewollt ein anderes.
            # Die Daten sind sein voller Pfad, so führt die Auswahl zurück.
            self.model.addItem(name, current)
        for m in MODELS:
            self.model.addItem(m, m)
        self.model.setCurrentIndex(self.model.findData(current))
        self.model.currentIndexChanged.connect(self.on_model)
        self.model.setAccessibleName(tr("model"))
        g.addWidget(self.model)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        g.addWidget(self.progress)

        lay.addStretch(1)
        return scroll

    # --------------------------------------------------- Seite: Mikrofon
    def page_mic(self):
        scroll, lay = self.page()

        g = self.group(tr("sec_mic"), lay)
        self.mic = self.guard(QComboBox())
        self._fill_mics(self.cfg.mic)
        self.mic.currentIndexChanged.connect(self.save_settings)
        self.mic.setAccessibleName(tr("sec_mic"))
        mic_row = QHBoxLayout()
        mic_row.addWidget(self.mic, 1)
        self.mic_refresh = QPushButton(tr("ai_refresh"))
        self.mic_refresh.setAccessibleName(tr("mic_refresh"))
        self.mic_refresh.clicked.connect(self.on_mic_refresh)
        mic_row.addWidget(self.mic_refresh)
        g.addLayout(mic_row)
        test = QPushButton(tr("mic_test"))
        test.clicked.connect(self.on_mictest)
        g.addWidget(test)
        self.test_out = QLabel()
        self.test_out.setWordWrap(True)
        g.addWidget(self.test_out)
        lay.addStretch(1)
        return scroll

    # ----------------------------------------------- Seite: Beta (G111)
    def page_beta(self):
        """Noch nicht fertige Funktionen an einem Ort, klar als Beta
        gekennzeichnet — die übrigen Seiten bleiben dadurch kurz."""
        scroll, lay = self.page()

        self.desc(tr("beta_intro"), lay)

        # --- Auto-Stopp nach Stille ([vad], siehe vad.settings) ---
        vs = vad.settings()
        g = self.group(tr("sec_vad"), lay)
        self.vad_autostop = QCheckBox(tr("vad_autostop"))
        self.vad_autostop.setChecked(vs.autostop)
        self.vad_autostop.toggled.connect(self.save_settings)
        self.vad_autostop.toggled.connect(self._sync_vad_enabled)
        g.addWidget(self.vad_autostop)
        self.desc(tr("vad_autostop_hint"), g)
        self.vad_silence = self.guard(QDoubleSpinBox())
        self.vad_silence.setRange(*vad.AUTOSTOP_SILENCE_RANGE)
        self.vad_silence.setSingleStep(0.1)
        self.vad_silence.setDecimals(1)
        self.vad_silence.setSuffix(" s")
        self.vad_silence.setValue(vs.autostop_silence)
        self.vad_silence.valueChanged.connect(self.save_soon)
        self.labeled_row(tr("vad_silence"), self.vad_silence, g)
        self.vad_backend = self.guard(QComboBox())
        for val in ("auto", "rms"):
            self.vad_backend.addItem(tr("vad_backend_" + val), val)
        self.select_data(self.vad_backend, vs.backend)
        self.vad_backend.currentIndexChanged.connect(self.save_settings)
        self.labeled_row(tr("vad_backend"), self.vad_backend, g)
        self.vad_model = QLineEdit(vs.silero_model)
        self.vad_model.editingFinished.connect(self.save_settings)
        self.vad_model_pick = QPushButton(tr("choose_file"))
        self.vad_model_pick.setAccessibleName(tr("choose_file") + " " + tr("vad_silero_model"))
        self.vad_model_pick.clicked.connect(
            lambda: self._pick_model_file(self.vad_model))
        self._file_row(tr("vad_silero_model"), self.vad_model, self.vad_model_pick, g)
        self.desc(tr("vad_silero_hint"), g)
        self._sync_vad_enabled(vs.autostop)

        # --- Vorlaufpuffer ([speech] preroll_ms, 0–2000 ms) ---
        g = self.group(tr("sec_preroll"), lay)
        self.preroll = self.guard(QSpinBox())
        self.preroll.setRange(0, 2000)
        self.preroll.setSingleStep(100)
        self.preroll.setSuffix(" ms")
        self.preroll.setSpecialValueText(tr("preroll_off"))
        self.preroll.setValue(self.cfg.preroll_ms)
        self.preroll.valueChanged.connect(self.save_soon)
        self.labeled_row(tr("preroll_ms"), self.preroll, g)
        self.desc(tr("preroll_hint"), g)
        self.preroll_mac_note = None
        if IS_MAC:
            self.preroll_mac_note = self.desc(tr("preroll_mac_hint"), g)
        g = self.group(tr("sec_wakeword"), lay)
        beta = self.desc(tr("wakeword_beta"), g)
        beta.setStyleSheet(f"color:{self._warn};font-weight:600;")   # Bernstein = Beta-Warnung
        self.wake_on = QCheckBox(tr("wakeword_enable"))
        self.wake_on.setChecked(self.cfg.wakeword_enabled)
        self.wake_on.toggled.connect(self.save_settings)
        g.addWidget(self.wake_on)
        self.desc(tr("wakeword_hint"), g)
        self.wake_phrase = QLineEdit(self.cfg.wakeword_phrase)
        self.wake_phrase.setPlaceholderText(config.WAKEWORD_DEFAULT)
        self.wake_phrase.editingFinished.connect(self.save_settings)
        self.labeled_row(tr("wakeword_phrase"), self.wake_phrase, g)
        self.desc(tr("wakeword_change_hint"), g)
        ws = wakeword.wake_settings()
        self.wake_model = QLineEdit(ws.spotter_model)
        self.wake_model.editingFinished.connect(self.save_settings)
        self.wake_model_pick = QPushButton(tr("choose_file"))
        self.wake_model_pick.setAccessibleName(tr("choose_file") + " " + tr("wake_spotter_model"))
        self.wake_model_pick.clicked.connect(lambda: self._pick_model_file(self.wake_model))
        self._file_row(tr("wake_spotter_model"), self.wake_model, self.wake_model_pick, g)
        self.desc(tr("wake_spotter_hint"), g)
        self.wake_threshold = self.guard(QDoubleSpinBox())
        self.wake_threshold.setRange(0.05, 0.99)
        self.wake_threshold.setSingleStep(0.05)
        self.wake_threshold.setDecimals(2)
        self.wake_threshold.setValue(ws.spotter_threshold)
        self.wake_threshold.valueChanged.connect(self.save_soon)
        self.labeled_row(tr("wake_threshold"), self.wake_threshold, g)
        self.desc(tr("wake_threshold_hint"), g)
        self.wake_verify = QCheckBox(tr("wake_verify"))
        self.wake_verify.setChecked(ws.verify)
        self.wake_verify.toggled.connect(self.save_settings)
        g.addWidget(self.wake_verify)

        lay.addStretch(1)
        return scroll

    def on_profiles_open(self):
        """Profil-Datei mit den Vorgaben anlegen, falls es sie nicht gibt, und
        im Texteditor öffnen. Eine vorhandene Datei bleibt unangetastet; der
        Pfad steht immer darunter, auch wenn sich kein Editor öffnen ließ."""
        path = profiles.PROFILES
        try:
            created = profiles.write_defaults(path)
        except OSError:
            self.profiles_status.setText("✕ " + tr("profiles_failed"))
            return
        if not open_text_file(path):
            self.profiles_status.setText("✕ " + tr("profiles_open_failed", path=path))
            return
        self.profiles_status.setText(tr("profiles_created" if created else "profiles_opened",
                                        path=path))

    def _file_row(self, label, edit, button, layout):
        """Beschriftung, Pfadfeld und „Wählen…" in einer Zeile."""
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setBuddy(edit)
        edit.setAccessibleName(label)
        row.addWidget(lbl)
        row.addWidget(edit, 1)
        row.addWidget(button)
        layout.addLayout(row)

    def _pick_model_file(self, edit):
        path, _ = QFileDialog.getOpenFileName(self, tr("choose_file"), edit.text(),
                                              "ONNX (*.onnx);;* (*)")
        if path:
            edit.setText(path)
            self.save_settings()

    def _sync_vad_enabled(self, on):
        self.vad_silence.setEnabled(on)

    def _fill_mics(self, selected):
        """Mikrofonliste neu aufbauen. Ein gespeichertes Gerät, das gerade
        nicht angeschlossen ist, bleibt als Eintrag ausgewählt — sonst setzte
        schon das Öffnen des Fensters die Wahl still auf den Standard zurück."""
        self.mic.blockSignals(True)
        self.mic.clear()
        self.mic.addItem(tr("mic_default"), "default")
        for name, desc in list_mics():
            self.mic.addItem(desc, name)
        if selected not in ("", "default") and self.mic.findData(selected) < 0:
            self.mic.addItem(selected, selected)
        self.select_data(self.mic, selected)
        self.mic.blockSignals(False)

    def on_mic_refresh(self):
        """Neu angeschlossene Mikrofone ohne Neustart des Fensters zeigen."""
        self._fill_mics(self.mic.currentData())

    # -------------------------------------------------- Seite: Wörterbuch
    def page_dict(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_dict"), lay)
        self.desc(tr("dict_hint"), g)
        # Nur die eigenen Wörter: stünden die gelernten hier, schriebe das erste
        # Speichern sie ins eigene Wörterbuch, wo sie nie mehr verfallen.
        self.dict_edit = QPlainTextEdit("\n".join(config.dictionary_manual()))
        self.dict_effective = QLabel()
        self.dict_effective.setObjectName("desc")
        self.dict_effective.setWordWrap(True)
        self.dict_timer = QTimer(self)
        self.dict_timer.setSingleShot(True)
        self.dict_timer.timeout.connect(
            lambda: config.dictionary_save(self.dict_edit.toPlainText()))
        self.dict_edit.textChanged.connect(self._update_dict_effective)
        self.dict_edit.setAccessibleName(tr("sec_dict"))
        self.dict_edit.textChanged.connect(lambda: self.dict_timer.start(800))
        g.addWidget(self.dict_edit)
        g.addWidget(self.dict_effective)
        self._update_dict_effective()
        self.auto_learn = QCheckBox(tr("auto_learn"))
        self.auto_learn.setChecked(self.cfg.auto_learn)
        self.auto_learn.toggled.connect(self.save_settings)
        g.addWidget(self.auto_learn)
        self.desc(tr("auto_learn_hint"), g)
        self.learned_label = QLabel(tr("learned_title"))
        g.addWidget(self.learned_label)
        self.learned_list = QListWidget()
        self.learned_list.setAccessibleName(tr("learned_title"))
        self.learned_list.setMaximumHeight(160)
        self.learned_label.setBuddy(self.learned_list)
        g.addWidget(self.learned_list)
        row = QHBoxLayout()
        self.learned_keep = QPushButton(tr("learned_keep"))
        self.learned_keep.setAccessibleName(tr("learned_keep"))
        self.learned_keep.clicked.connect(self.on_learned_keep)
        self.learned_forget = QPushButton(tr("learned_forget"))
        self.learned_forget.setAccessibleName(tr("learned_forget"))
        self.learned_forget.clicked.connect(self.on_learned_forget)
        row.addWidget(self.learned_keep)
        row.addWidget(self.learned_forget)
        row.addStretch(1)
        g.addLayout(row)
        self.desc(tr("learned_hint"), g)
        self._fill_learned()
        return scroll

    def _fill_learned(self):
        import datetime
        self.learned_list.clear()
        items = config.dictionary_learned()
        for item in items:
            since = datetime.date.fromtimestamp(item["first"]).strftime("%d.%m.%Y")
            row = QListWidgetItem(tr("learned_row", word=item["word"], since=since,
                                     seen=item["seen"]))
            row.setData(Qt.UserRole, item["word"])
            self.learned_list.addItem(row)
        if not items:
            self.learned_list.addItem(QListWidgetItem(tr("learned_none")))
            self.learned_list.item(0).setFlags(Qt.NoItemFlags)
        for button in (self.learned_keep, self.learned_forget):
            button.setEnabled(bool(items))
        self._update_dict_effective()

    def _selected_learned(self):
        item = self.learned_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def on_learned_keep(self):
        word = self._selected_learned()
        if not word:
            return
        if self.dict_timer.isActive():         # erst die eigene Eingabe sichern
            self.dict_timer.stop()
            config.dictionary_save(self.dict_edit.toPlainText())
        config.dictionary_keep(word)
        self.dict_edit.blockSignals(True)
        self.dict_edit.setPlainText("\n".join(config.dictionary_manual()))
        self.dict_edit.blockSignals(False)
        self._fill_learned()

    def on_learned_forget(self):
        word = self._selected_learned()
        if not word:
            return
        config.dictionary_forget(word)
        self._fill_learned()

    def _update_dict_effective(self):
        words = [word.strip() for word in self.dict_edit.toPlainText().splitlines()
                 if word.strip()]
        if hasattr(self, "learned_list"):
            have = {w.casefold() for w in words}
            words += [item["word"] for item in config.dictionary_learned()
                      if item["word"].casefold() not in have]
        effective = dictprompt.effective_words(
            words, dictprompt.FULL_DICTIONARY_TOKEN_BUDGET)
        self.dict_effective.setText(tr("dict_effective", count=len(effective),
                                       words=", ".join(effective) or "—"))

    # ------------------------------------------------ Seite: Textersetzungen
    def page_replace(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_replace"), lay)
        self.replace_on = QCheckBox(tr("replace_enable"))
        self.replace_on.setChecked(self.cfg.text_replace)
        self.replace_on.toggled.connect(self.save_settings)
        g.addWidget(self.replace_on)
        self.desc(tr("replace_hint"), g)
        self.replace_edit = QPlainTextEdit(config.replacement_text())
        self.replace_timer = QTimer(self)
        self.replace_timer.setSingleShot(True)
        self.replace_timer.timeout.connect(
            lambda: config.replacement_save(self.replace_edit.toPlainText()))
        self.replace_edit.textChanged.connect(lambda: self.replace_timer.start(800))
        self.replace_edit.setAccessibleName(tr("sec_replace"))
        g.addWidget(self.replace_edit)
        return scroll

    # --------------------------------------------- Seite: Datei transkribieren
    def page_file(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_file"), lay)
        self.desc(tr("file_hint_manual"), g)
        self.file_format = self.guard(QComboBox())
        for fmt in ("text", "srt", "vtt"):
            self.file_format.addItem(tr("file_format_" + fmt), fmt)
        self.labeled_row(tr("file_format"), self.file_format, g)
        row = QHBoxLayout()
        self.file_pick = QPushButton(tr("file_pick"))
        self.file_pick.clicked.connect(self.on_pick_file)
        row.addWidget(self.file_pick)
        self.file_cancel = QPushButton(tr("file_cancel"))
        self.file_cancel.setEnabled(False)
        self.file_cancel.clicked.connect(self.on_file_cancel)
        row.addWidget(self.file_cancel)
        row.addStretch(1)
        g.addLayout(row)
        self.file_bar = QProgressBar()
        self.file_bar.setRange(0, 1000)
        self.file_bar.setTextVisible(False)
        self.file_bar.setAccessibleName(tr("file_progress"))
        self.file_bar.setVisible(False)
        g.addWidget(self.file_bar)
        self.file_status = QLabel()
        self.file_status.setObjectName("desc")
        self.file_status.setWordWrap(True)
        g.addWidget(self.file_status)
        self.file_result = QPlainTextEdit()
        self.file_result.setReadOnly(True)
        self.file_result.setAccessibleName(tr("sec_file"))
        g.addWidget(self.file_result)
        out_row = QHBoxLayout()
        self.file_copy = QPushButton(tr("copy"))
        self.file_copy.clicked.connect(self.on_file_copy)
        out_row.addWidget(self.file_copy)
        self.file_save = QPushButton(tr("file_save"))
        self.file_save.setEnabled(False)
        self.file_save.clicked.connect(self.on_file_save)
        out_row.addWidget(self.file_save)
        out_row.addStretch(1)
        g.addLayout(out_row)
        self._file_cancel_event = None
        self._file_source = ""          # Quelle und Format des Ergebnisses im Feld
        self._file_result_fmt = "text"
        return scroll

    # --------------------------------------------------------- Seite: KI (Ollama)
    def _fill_ai_modes(self, combo):
        combo.clear()
        for key in aimodes.builtin_keys():
            combo.addItem(tr("ai_mode_" + key), key)
        for name in aimodes.parse_custom(config.ai_modes_text()):
            combo.addItem(name, name)

    def page_ai(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_ai"), lay)
        self.ai_on = QCheckBox(tr("ai_enable"))
        self.ai_on.setChecked(self.cfg.ai_enabled)
        self.ai_on.toggled.connect(self.save_settings)
        g.addWidget(self.ai_on)
        self.desc(tr("ai_hint"), g)
        self.ai_endpoint = QLineEdit(self.cfg.ai_endpoint)
        self.ai_endpoint.setPlaceholderText(config.OLLAMA_DEFAULT)
        self.ai_endpoint.editingFinished.connect(self.save_settings)
        self.labeled_row(tr("ai_endpoint"), self.ai_endpoint, g)
        mrow = QHBoxLayout()
        model_lbl = QLabel(tr("ai_model"))
        mrow.addWidget(model_lbl)
        self.ai_model = self.guard(QComboBox())
        self.ai_model.setEditable(True)
        model_lbl.setBuddy(self.ai_model)
        self.ai_model.setAccessibleName(tr("ai_model"))
        if self.cfg.ai_model:
            self.ai_model.addItem(self.cfg.ai_model, self.cfg.ai_model)
        self.ai_model.currentTextChanged.connect(self.save_soon)
        mrow.addWidget(self.ai_model, 1)
        refresh = QPushButton(tr("ai_refresh"))
        refresh.clicked.connect(self.on_ai_refresh)
        mrow.addWidget(refresh)
        g.addLayout(mrow)
        self.ai_status = QLabel()
        self.ai_status.setObjectName("desc")
        self.ai_status.setWordWrap(True)
        g.addWidget(self.ai_status)
        self.ai_post = QCheckBox(tr("ai_post_enable"))
        self.ai_post.setChecked(self.cfg.ai_post_process)
        self.ai_post.toggled.connect(self.save_settings)
        g.addWidget(self.ai_post)
        self.ai_mode = self.guard(QComboBox())
        self._fill_ai_modes(self.ai_mode)
        self.select_data(self.ai_mode, self.cfg.ai_post_mode)
        self.ai_mode.currentIndexChanged.connect(self.save_settings)
        self.labeled_row(tr("ai_post_mode"), self.ai_mode, g)
        self.ai_voice = QCheckBox(tr("ai_voice_enable"))
        self.ai_voice.setChecked(self.cfg.ai_voice_modes)
        self.ai_voice.toggled.connect(self.save_settings)
        g.addWidget(self.ai_voice)
        self.desc(tr("ai_voice_hint"), g)
        trow = QHBoxLayout()
        testb = QPushButton(tr("ai_test"))
        testb.clicked.connect(self.on_ai_test)
        trow.addWidget(testb)
        trow.addStretch(1)
        g.addLayout(trow)
        self.ai_test_lbl = QLabel()
        self.ai_test_lbl.setObjectName("desc")
        self.ai_test_lbl.setWordWrap(True)
        g.addWidget(self.ai_test_lbl)

        g = self.group(tr("sec_cleanup"), lay)
        self.cleanup_on = QCheckBox(tr("cleanup_enable"))
        self.cleanup_on.setChecked(self.cfg.cleanup_enabled)
        self.cleanup_on.toggled.connect(self._on_cleanup_toggled)
        g.addWidget(self.cleanup_on)
        self.cleanup_level = self.guard(QComboBox())
        for level in cleanup.LEVELS:
            self.cleanup_level.addItem(tr("cleanup_level_" + level), level)
        self.select_data(self.cleanup_level, self.cfg.cleanup_level)
        self.cleanup_level.currentIndexChanged.connect(self.save_settings)
        self.labeled_row(tr("cleanup_level"), self.cleanup_level, g)
        self.cleanup_level.setEnabled(self.cfg.cleanup_enabled)
        self.desc(tr("cleanup_hint"), g)
        rrow = QHBoxLayout()
        self.raw_back_btn = QPushButton(tr("raw_back"))
        self.raw_back_btn.clicked.connect(self.on_raw_back)
        rrow.addWidget(self.raw_back_btn)
        rrow.addStretch(1)
        g.addLayout(rrow)
        self.desc(tr("raw_back_hint"), g)
        self.raw_back_status = QLabel()
        self.raw_back_status.setObjectName("desc")
        self.raw_back_status.setWordWrap(True)
        g.addWidget(self.raw_back_status)

        g = self.group(tr("sec_ai_modes"), lay)
        self.desc(tr("ai_modes_hint"), g)
        self.ai_modes_edit = QPlainTextEdit(config.ai_modes_text())
        self.ai_modes_timer = QTimer(self)
        self.ai_modes_timer.setSingleShot(True)
        self.ai_modes_timer.timeout.connect(self._save_ai_modes)
        self.ai_modes_edit.textChanged.connect(lambda: self.ai_modes_timer.start(800))
        self.ai_modes_edit.setAccessibleName(tr("sec_ai_modes"))
        g.addWidget(self.ai_modes_edit)
        lay.addStretch(1)
        if self.cfg.ai_enabled:
            QTimer.singleShot(200, self.on_ai_refresh)
        return scroll

    def _on_cleanup_toggled(self, on):
        """Einschalten mit Stufe „Aus" bewirkte nichts: dann „Leicht" wählen
        (ohne KI, nur Füllwörter und Wiederholungen)."""
        self.cleanup_level.setEnabled(on)
        if on and self.cleanup_level.currentData() == "aus":
            self.cleanup_level.blockSignals(True)
            self.select_data(self.cleanup_level, "leicht")
            self.cleanup_level.blockSignals(False)
        self.save_settings()

    def on_raw_back(self):
        """Den Rohtext des letzten Diktats anfordern. Er liegt nur im Speicher
        des Diktat-Dienstes: Windows/macOS über die App im selben Prozess,
        Linux per SIGHUP an den Diktat-Dienst des eigenen Profils."""
        if self.controller is not None and hasattr(self.controller, "raw_back"):
            ok = self.controller.raw_back()
        elif not (IS_WINDOWS or IS_MAC):
            ok = sysctl("kill", "-s", "HUP", daemon_unit()).returncode == 0
        else:
            ok = False
        self.raw_back_status.setText(tr("raw_back_sent") if ok
                                     else tr("raw_back_unavailable"))

    def _save_ai_modes(self):
        config.ai_modes_save(self.ai_modes_edit.toPlainText())
        cur = self.ai_mode.currentData()
        self._fill_ai_modes(self.ai_mode)
        self.select_data(self.ai_mode, cur)

    def on_ai_refresh(self):
        ep = self._confirmed_ai_endpoint()
        if ep is None:
            return
        self.ai_status.setText(tr("ai_testing"))

        def run():
            # Ollama (/api/tags) oder OpenAI-kompatibel (/v1/models), ein Weg.
            kind, models = ai.probe(ep)
            self.bridge.ai_models.emit({"available": kind is not None,
                                        "models": models})
        threading.Thread(target=run, daemon=True).start()

    def on_ai_models(self, result):
        models = result.get("models", [])
        cur = self.ai_model.currentText().strip()
        self.ai_model.blockSignals(True)
        self.ai_model.clear()
        for m in models:
            self.ai_model.addItem(m, m)
        if cur and cur not in models:
            self.ai_model.insertItem(0, cur, cur)
        idx = self.ai_model.findText(cur) if cur else -1
        self.ai_model.setCurrentIndex(idx if idx >= 0 else 0)
        self.ai_model.blockSignals(False)
        if models:
            self.ai_status.setText("")
        else:
            self.ai_status.setText(tr("ai_no_models") if result.get("available")
                                   else tr("ai_unavailable"))

    def on_ai_test(self):
        ep = self._confirmed_ai_endpoint()
        if ep is None:
            return
        self.ai_test_lbl.setText(tr("ai_testing"))
        model = self.ai_model.currentText().strip()
        timeout = self.cfg.ai_timeout
        sample = "um so like we should uh ship it on friday yeah"
        system = aimodes.system_for("cleanup")

        def run():
            # Der Weg, den der Endpunkt spricht: OpenAI-kompatible Server kennen
            # /api/generate nicht und bekommen den Bereinigungsaufruf (/v1).
            if ai.probe(ep, timeout=2.0)[0] == "openai":
                out, _reason = ai.chat_cleanup(sample, model=model, endpoint=ep,
                                               timeout=timeout)
            else:
                out = ai.generate(sample, model=model, system=system,
                                  endpoint=ep, timeout=timeout)
            self.bridge.ai_test_done.emit(out or "")
        threading.Thread(target=run, daemon=True).start()

    def on_ai_test_done(self, out):
        if out:
            self.ai_test_lbl.setText("✓ " + tr("ai_test_ok", text=out[:120]))
        else:
            self.ai_test_lbl.setText("✕ " + tr("ai_test_fail"))

    def _confirmed_ai_endpoint(self):
        endpoint = self.ai_endpoint.text().strip() or config.OLLAMA_DEFAULT
        if endpoint == self._ai_endpoint_ok:
            return endpoint
        warning = ai.endpoint_warning(endpoint)
        if warning == "invalid":
            QMessageBox.warning(self, tr("ai_endpoint_invalid_title"),
                                tr("ai_endpoint_invalid"))
            self.ai_endpoint.setText(self.cfg.ai_endpoint)
            return None
        if warning in ("remote", "remote_plain"):
            message = tr("ai_endpoint_remote_plain" if warning == "remote_plain"
                         else "ai_endpoint_remote")
            answer = QMessageBox.question(self, tr("ai_endpoint_warning_title"), message,
                                          QMessageBox.Yes | QMessageBox.No,
                                          QMessageBox.No)
            if answer != QMessageBox.Yes:
                self.ai_endpoint.setText(self.cfg.ai_endpoint)
                return None
        self._ai_endpoint_ok = endpoint
        return endpoint

    # ----------------------------------------------------- Seite: Verlauf
    def page_history(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_history"), lay)
        self.hist_enable = QCheckBox(tr("history_enable"))
        self.hist_enable.setChecked(self.cfg.history_enabled)
        self.hist_enable.toggled.connect(self.save_settings)
        g.addWidget(self.hist_enable)
        self.desc(tr("history_copy"), g)
        self.hist = QListWidget()
        self.hist.itemClicked.connect(self.on_hist_click)
        self.hist.setAccessibleName(tr("sec_history"))
        g.addWidget(self.hist)
        self.hist_msg = QLabel()
        self.hist_msg.setObjectName("desc")
        g.addWidget(self.hist_msg)
        clear = QPushButton(tr("history_clear"))
        clear.clicked.connect(self.on_hist_clear)
        g.addWidget(clear)
        self.reload_history()
        return scroll

    # ------------------------------------------------------ Seite: System
    def page_system(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_system"), lay)
        self.auto = QCheckBox(tr("autostart"))
        self.auto.setChecked(autostart_enabled())
        if profile_autostart_blocked():
            self.auto.setEnabled(False)
            self.auto.setToolTip(
                "Profile autostart is unavailable on this platform. "
                "Profil-Autostart ist auf dieser Plattform nicht verfügbar.")
        self.auto.toggled.connect(self.on_autostart)
        g.addWidget(self.auto)
        self.uilang = self.guard(QComboBox())
        for val, label in (("auto", tr("ui_auto")), ("de", "Deutsch"), ("en", "English")):
            self.uilang.addItem(label, val)
        self.uilang.setCurrentIndex({"auto": 0, "de": 1, "en": 2}.get(self.cfg.ui_language, 0))
        self.uilang.currentIndexChanged.connect(self.on_uilang_changed)
        self.labeled_row(tr("ui_language"), self.uilang, g)

        # --- Aktualisierungen ---
        g = self.group(tr("update_label"), lay)
        self.update_startup = QCheckBox(tr("update_check_startup"))
        self.update_startup.setChecked(self.cfg.update_check)
        self.update_startup.toggled.connect(self.save_settings)
        g.addWidget(self.update_startup)
        row = QHBoxLayout()
        check_btn = QPushButton(tr("update_check_now"))
        check_btn.clicked.connect(self.on_check_update)
        row.addWidget(check_btn)
        self.update_get = QPushButton(tr("update_get"))
        self.update_get.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/Skryx-L-A/quassel/releases/latest")))
        self.update_get.setVisible(False)
        row.addWidget(self.update_get)
        row.addStretch(1)
        g.addLayout(row)
        self.update_status = QLabel()
        self.update_status.setObjectName("desc")
        self.update_status.setWordWrap(True)
        g.addWidget(self.update_status)

        # --- Zurücksetzen (#31) ---
        g = self.group(tr("sec_reset"), lay)
        self.desc(tr("reset_hint"), g)
        reset_btn = QPushButton(tr("reset_defaults"))
        reset_btn.clicked.connect(self.on_reset_defaults)
        g.addWidget(reset_btn)
        self.reset_status = QLabel()
        self.reset_status.setObjectName("desc")
        g.addWidget(self.reset_status)

        lay.addStretch(1)
        return scroll

    # ------------------------------------------------------- Seite: Über
    def page_about(self):
        scroll, lay = self.page()
        g = self.group(tr("about"), lay)
        row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(app_icon().pixmap(44, 44))
        row.addWidget(icon_lbl)
        about = QLabel(tr("about_text", version=__version__))
        about.setWordWrap(True)
        row.addWidget(about, 1)
        g.addLayout(row)
        link = QPushButton(tr("repo"))
        link.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/Skryx-L-A/quassel")))
        g.addWidget(link)

        # --- Diagnose kopieren ---
        g = self.group(tr("sec_diag"), lay)
        row = QHBoxLayout()
        self.diag_copy = QPushButton(tr("diag_copy"))
        self.diag_copy.setAccessibleName(tr("diag_copy"))
        self.diag_copy.setAccessibleDescription(tr("diag_hint"))
        self.diag_copy.clicked.connect(lambda: self.on_diag("copy"))
        row.addWidget(self.diag_copy)
        self.diag_save = QPushButton(tr("diag_save"))
        self.diag_save.setAccessibleName(tr("diag_save"))
        self.diag_save.clicked.connect(lambda: self.on_diag("save"))
        row.addWidget(self.diag_save)
        row.addStretch(1)
        g.addLayout(row)
        self.diag_hint = self.desc(tr("diag_hint"), g)
        self.diag_status = QLabel()
        self.diag_status.setObjectName("desc")
        self.diag_status.setWordWrap(True)
        g.addWidget(self.diag_status)

        lay.addStretch(1)
        return scroll

    # ------------------------------------------------------------- Status
    def chord_label(self):
        return tr(config.CHORD_LABEL_KEYS[self.cfg.chord]).split("(")[0].strip()

    def refresh_status(self):
        if self.controller is not None:
            on = self.controller.enabled
        else:
            on = daemon_active()
        color = self._accent if on else "#8a8a96"
        self.status_dot.setStyleSheet(f"background: {color}; border-radius: 6px;")
        self.status_lbl.setText(tr("on") if on else tr("off"))
        self.toggle_btn.setText(tr("turn_off") if on else tr("turn_on"))
        self._refresh_stats()       # Statistik-Seite live halten

    def on_toggle(self):
        if self.controller is not None:
            self.controller.toggle()    # hält Tray-Menü und Pille synchron
        elif daemon_active():
            sysctl("stop", *service_units(start=False))
        else:
            sysctl("start", *service_units(start=True))
        self.refresh_status()

    def reset_pill_position(self):
        """Verwirft eine gespeicherte Ziehposition; die Pille springt über
        ihren eigenen 1-Sekunden-Konfig-Timer zurück auf unten mittig — auch
        wenn 'frei verschiebbar' gerade aus ist."""
        config.save({("pill", "pos_x"): -1, ("pill", "pos_y"): -1})

    # ----------------------------------------------------------- Speichern
    def save_settings(self, *_a):
        if self._loading:
            return
        self.save_timer.stop()
        endpoint = self._confirmed_ai_endpoint()
        if endpoint is None:
            return
        werte = {
            ("pill", "enabled"): str(self.pill_show.isChecked()).lower(),
            ("pill", "scale"): self.pill_size.value() / 100,
            ("pill", "opacity"): self.pill_opacity.value() / 100,
            ("pill", "movable"): str(self.pill_movable.isChecked()).lower(),
            ("pill", "click_opens_center"): str(self.pill_click_center.isChecked()).lower(),
            ("pill", "show_preview"): str(self.pill_preview.isChecked()).lower(),
            ("hotkey", "chord"): self.chord.currentData(),
            ("behavior", "mute_while_dictating"): self.mute_combo.currentData(),
            ("behavior", "beep"): str(self.beep_chk.isChecked()).lower(),
            ("behavior", "text_replace"): str(self.replace_on.isChecked()).lower(),
            ("behavior", "stats_enabled"): str(self.stats_on.isChecked()).lower(),
            ("speech", "language"): self.lang.currentData(),
            ("speech", "punctuation"): str(self.punct.isChecked()).lower(),
            ("speech", "commands"): str(self.cmds.isChecked()).lower(),
            ("speech", "programmer_mode"): str(self.progmode.isChecked()).lower(),
            ("speech", "auto_learn"): str(self.auto_learn.isChecked()).lower(),
            ("speech", "mic"): self.mic.currentData(),
            ("streaming", "enabled"): str(self.stream_on.isChecked()).lower(),
            ("streaming", "mode"): self.stream_mode.currentData(),
            ("wakeword", "enabled"): str(self.wake_on.isChecked()).lower(),
            ("wakeword", "phrase"): self.wake_phrase.text().strip() or config.WAKEWORD_DEFAULT,
            ("history", "enabled"): str(self.hist_enable.isChecked()).lower(),
            ("system", "update_check"): str(self.update_startup.isChecked()).lower(),
            ("ai", "enabled"): str(self.ai_on.isChecked()).lower(),
            ("ai", "endpoint"): endpoint,
            ("ai", "model"): self.ai_model.currentText().strip(),
            ("ai", "post_process"): str(self.ai_post.isChecked()).lower(),
            ("ai", "post_mode"): self.ai_mode.currentData() or "cleanup",
            ("ai", "voice_modes"): str(self.ai_voice.isChecked()).lower(),
            ("wakeword", "spotter_model"): self.wake_model.text().strip(),
            ("wakeword", "spotter_threshold"): round(self.wake_threshold.value(), 2),
            ("wakeword", "verify"): str(self.wake_verify.isChecked()).lower(),
            ("speech", "preroll_ms"): self.preroll.value(),
            ("vad", "autostop"): str(self.vad_autostop.isChecked()).lower(),
            ("vad", "autostop_silence"): round(self.vad_silence.value(), 1),
            ("vad", "backend"): self.vad_backend.currentData() or "auto",
            ("vad", "silero_model"): self.vad_model.text().strip(),
            ("cleanup", "enabled"): str(self.cleanup_on.isChecked()).lower(),
            ("cleanup", "level"): self.cleanup_level.currentData() or "aus",
            ("ui", "language"): self.uilang.currentData(),
        }
        # Nur schreiben, wo es das Auswahlfeld überhaupt gibt. Auf Linux und
        # Windows würde der Schlüssel sonst bei jedem Speichern auf "system"
        # gesetzt — und eine vom Mac mitgenommene config.ini verlöre dort ihre
        # Gerätewahl, ohne dass jemand etwas umgestellt hätte.
        if self.beep_out is not None:
            werte[("behavior", "beep_output")] = self.beep_out.currentData()
        config.save(werte)
        self.cfg.reload(force=True)
        self.hint.setText(tr("hint", chord=self.chord_label()))

    def on_uilang_changed(self, *_a):
        """App-Sprache ohne Neustart wechseln: speichern, Sprache setzen und
        das Fenster neu aufbauen. Der Neuaufbau wird verzögert (singleShot),
        damit nicht das gerade sendende Dropdown mitten im Signal gelöscht
        wird."""
        if self._loading:
            return
        self.save_settings()
        lang = self.uilang.currentData()
        i18n.set_language(None if lang == "auto" else lang)
        QTimer.singleShot(0, self._rebuild_ui)

    def _rebuild_ui(self):
        row = self.sidebar.currentRow()
        self._loading = True
        self.build()
        self._loading = False
        if 0 <= row < self.sidebar.count():
            self.sidebar.setCurrentRow(row)
        self.refresh_status()
        # Windows: Tray-Menü der Hintergrund-App in der neuen Sprache nachziehen
        if self.controller is not None and hasattr(self.controller, "retranslate"):
            self.controller.retranslate()

    def _sync_streaming_enabled(self, on):
        self.stream_mode.setEnabled(on)

    # ----------------------------------------------------- Seite: Statistik (#22)
    def _metric_card(self, caption):
        frame = QFrame()
        frame.setObjectName("metric")
        frame.setStyleSheet(
            "QFrame#metric{border:1px solid palette(midlight);border-radius:12px;}")
        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(2)
        val = QLabel("0")
        val.setStyleSheet(f"font-size:26px;font-weight:800;color:{self._accent};")
        cap = QLabel(caption)
        cap.setObjectName("desc")
        cap.setWordWrap(True)
        v.addWidget(val)
        v.addWidget(cap)
        return val, frame

    def page_stats(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_stats"), lay)
        self.stats_on = QCheckBox(tr("stats_enable"))
        self.stats_on.setChecked(self.cfg.stats_enabled)
        self.stats_on.toggled.connect(self.save_settings)
        g.addWidget(self.stats_on)
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.m_total, c1 = self._metric_card(tr("stats_total_words"))
        self.m_spoken, c2 = self._metric_card(tr("stats_time_spoken"))
        self.m_today, c3 = self._metric_card(tr("stats_words_today"))
        for c in (c1, c2, c3):
            cards.addWidget(c, 1)
        g.addLayout(cards)

        g2 = self.group(tr("stats_chart"), lay)
        self.stats_chart = _StatsChart(self._accent)
        self.stats_chart.setAccessibleName(tr("stats_chart"))
        g2.addWidget(self.stats_chart)
        self.stats_empty = self.desc(tr("stats_chart_empty"), g2)
        self.stats_saved_lbl = QLabel()
        self.stats_saved_lbl.setObjectName("desc")
        g2.addWidget(self.stats_saved_lbl)

        reset = QPushButton(tr("stats_reset"))
        reset.clicked.connect(self.on_stats_reset)
        lay.addWidget(reset)
        lay.addStretch(1)
        self._refresh_stats()
        return scroll

    @staticmethod
    def _fmt_duration(seconds):
        seconds = int(seconds or 0)
        if seconds < 60:
            return f"{seconds} s"
        m, s = divmod(seconds, 60)
        if m < 60:
            return f"{m} min"
        h, m = divmod(m, 60)
        return f"{h} h {m} min"

    def _refresh_stats(self):
        if not hasattr(self, "stats_chart"):
            return
        s = stats.summary()
        self.m_total.setText(f"{s.get('words', 0):,}")
        self.m_spoken.setText(self._fmt_duration(s.get("seconds_spoken", 0.0)))
        self.m_today.setText(f"{stats.words_today(s):,}")
        series = stats.daily_series(14, s)
        self.stats_chart.set_series(series)
        self.stats_empty.setVisible(not any(v > 0 for _, v in series))
        saved_min = int(round(s.get("seconds_saved", 0.0) / 60))
        self.stats_saved_lbl.setText(f"{tr('stats_saved')}: ~{saved_min:,} min")

    def on_stats_reset(self):
        stats.reset()
        self._refresh_stats()

    # ----------------------------------------------------- Erst-Einrichtung (#27)
    def _onboard_if_shown(self):
        """Erst-Einrichtung nur für ein Fenster, das tatsächlich gezeigt wird
        (main() ruft show() vor dem ersten Ereignis). Ein nie gezeigter oder
        schon geschlossener Center öffnete sonst später einen modalen Dialog
        aus dem Nichts, in Tests mitten in einem fremden Test."""
        if self.isVisible():
            self._maybe_onboard()

    def _maybe_onboard(self):
        if self.cfg.onboarded:
            return
        dlg = OnboardingDialog(self, self.chord_label(),
                               record_fn=self._mictest_record)
        dlg.exec()
        if dlg.open_page == "wake":
            self.show_page("nav_beta")          # dort steht das Wake-Word
        elif dlg.open_page == "speech":
            self.show_page("nav_speech")
        config.save({("system", "onboarded"): "true"})
        self.cfg.reload(force=True)

    def on_autostart(self, on):
        if profile_autostart_blocked():
            return
        if IS_WINDOWS:
            autostart_set(on)
        elif IS_MAC:
            mac_autostart_set(on)
        else:
            sysctl("enable" if on else "disable", daemon_unit())

    # ------------------------------------------------------------ Verlauf
    def reload_history(self):
        self.hist.clear()
        for e in reversed(config.history_read()):
            text = e.get("text", "")
            self.hist.addItem(text if len(text) <= 90 else text[:89] + "…")
            self.hist.item(self.hist.count() - 1).setData(Qt.UserRole, text)

    def on_hist_click(self, item):
        clip_copy(item.data(Qt.UserRole))
        self.hist_msg.setText("✓ " + tr("copied"))

    def on_hist_clear(self):
        config.history_clear()
        self.reload_history()
        self.hist_msg.setText("")

    # ------------------------------------------------------------- Modell
    def on_model(self, *_a):
        if self._loading:
            return
        model = self.model.currentData()
        env = config.read_serverenv()
        if model not in MODELS:
            # Eigenes Modell (voller Pfad): zurückwechseln, nie herunterladen.
            if model != env.get("MODEL_PATH"):
                self.switch_model(model, os.path.basename(model))
            return
        modeldir = os.path.dirname(env.get("MODEL_PATH", "")) or \
            config.MODELDIR
        target = os.path.join(modeldir, f"ggml-{model}.bin")
        if os.path.exists(target) and os.path.getsize(target) > 1024:
            self.switch_model(target, model)
            return
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.test_out.setText(tr("downloading", model=model))

        def download():
            ok = net_download(MODEL_URL.format(model), target,
                              self.bridge.progress.emit)
            if ok:
                self.bridge.model_done.emit(target + "|" + model)
            else:
                self.bridge.message.emit("✕ " + tr("download_failed", err="network"))
        threading.Thread(target=download, daemon=True).start()

    def switch_model(self, path, model):
        env = config.read_serverenv()
        env["MODEL_PATH"] = path
        config.write_serverenv(env)
        restart_server()
        self.progress.setVisible(False)
        self.test_out.setText("✓ " + tr("model_switched", model=model))

    def on_progress(self, frac):
        self.progress.setValue(int(frac * 100))

    def on_message(self, msg):
        self.progress.setVisible(False)
        self.test_out.setText(msg)

    def on_model_done(self, payload):
        path, model = payload.rsplit("|", 1)
        self.switch_model(path, model)

    # ------------------------------------------------------ Mikrofon-Test
    def _mictest_record(self):
        """2,5 s Audio aufnehmen -> rohe PCM-Bytes (s16le, 16 kHz, mono)."""
        if IS_WINDOWS:
            from .win.audio_win import Recorder
            rec = Recorder()
            if not rec.start(self.cfg.mic):
                raise MicTestError(self._mic_open_error())
            time.sleep(2.5)
            rec.stop()
            return rec.raw_bytes()
        if IS_MAC and mac_backend() != "ffmpeg":
            # In-process über sounddevice (kein ffmpeg nötig). Eigene Rohdatei,
            # damit der Test eine laufende Aufnahme des Daemons nicht überschreibt.
            from .audio import Recorder
            rec = Recorder(raw_path=os.path.join(RUNDIR, "mictest.raw"))
            if not rec.start(self.cfg.mic):
                raise MicTestError(self._mic_open_error())
            time.sleep(2.5)
            rec.stop()
            return rec.raw_bytes()
        cmd = record_command(self.cfg.mic)
        if cmd is None:
            raise MicTestError(tr("err_recorder_missing", tool=(
                "ffmpeg" if IS_MAC else "pw-record/parecord")))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        try:
            data, _ = p.communicate(timeout=2.5)
        except subprocess.TimeoutExpired:
            p.send_signal(2)
            try:
                data, _ = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                data, _ = p.communicate()
        return data

    def _mic_open_error(self):
        """Warum ließ sich das Mikrofon nicht öffnen? Auf dem Mac zuerst die
        Freigabe prüfen — fehlt sie, hilft nur die Systemeinstellung."""
        if IS_MAC and mac_mic_permission() == "denied":
            return tr("err_mic_denied")
        return tr("err_mic_open")

    def _server_error(self):
        """Server antwortet nicht: fehlt das Modell, oder lädt/hängt er?"""
        return failure_text(None)

    def on_mictest(self):
        self.test_out.setText(tr("mic_testing"))
        threading.Thread(target=self._mictest_run, daemon=True).start()

    def _mictest_run(self):
        try:
            data = self._mictest_record()
        except MicTestError as e:
            self.bridge.message.emit("✕ " + e.args[0])
            return
        except Exception:  # noqa: BLE001 — Audio-Backend kaputt
            self.bridge.message.emit("✕ " + self._mic_open_error())
            return
        if not data or len(data) < 8000:
            self.bridge.message.emit("✕ " + tr("mic_nothing"))
            return
        self.bridge.message.emit(tr("mic_transcribing"))
        wavpath = os.path.join(config.DATADIR, "mictest.wav")
        os.makedirs(config.DATADIR, exist_ok=True)
        with wave.open(wavpath, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(data)
        try:
            if not whisperclient.ensure_server():
                self.bridge.message.emit("✕ " + self._server_error())
                return
            text = whisperclient.transcribe(wavpath, self.cfg, timeout=60)
        finally:
            os.unlink(wavpath)
        if text is None:
            self.bridge.message.emit("✕ " + tr("err_transcribe"))
            return
        text = " ".join(text.split()).strip() or "—"
        self.bridge.message.emit("✓ " + tr("mic_result", text=text))

    # ------------------------------------------------ Datei transkribieren (#21)
    def on_pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("file_pick"))
        if path:
            self.start_file(path)

    def start_file(self, path, run_async=True):
        """Datei transkribieren: Fortschritt und Abbruch laufen bis in
        transcribe_file() durch; das Ergebnis landet nur im Feld, kopiert wird
        erst auf Knopfdruck."""
        if not transcribe_file.is_supported(path):
            self.file_status.setText("✕ " + tr("file_failed"))
            return
        if not transcribe_file.have_ffmpeg() and not path.lower().endswith(".wav"):
            self.file_status.setText("✕ " + tr("file_no_ffmpeg"))
            return
        fmt = self.file_format.currentData() or "text"
        cancel = self._file_cancel_event = threading.Event()
        self.file_status.setText(tr("file_running", name=os.path.basename(path)))
        self.file_result.setPlainText("")
        self.file_save.setEnabled(False)
        self._file_source, self._file_result_fmt = path, fmt
        self.file_bar.setValue(0)
        self.file_bar.setVisible(True)
        self.file_pick.setEnabled(False)
        self.file_cancel.setEnabled(True)

        def run():
            try:
                text = transcribe_file.transcribe_file(
                    path, self.cfg, output_format=fmt, cancel_event=cancel,
                    progress=lambda done, total: self.bridge.file_progress.emit(
                        float(done), float(total)))
            except Exception:    # noqa: BLE001
                text = None
            if cancel.is_set():
                self.bridge.file_done.emit(tr("file_canceled"), "")
            elif not text or not text.strip():
                self.bridge.file_done.emit("✕ " + tr("file_failed"), "")
            else:
                text = text.strip()
                self.bridge.file_done.emit(
                    "✓ " + tr("file_ready", chars=len(text)), text)
        if run_async:
            threading.Thread(target=run, daemon=True).start()
        else:
            run()

    def on_file_progress(self, done, total):
        if total > 0:
            self.file_bar.setValue(int(1000 * min(1.0, done / total)))

    def on_file_cancel(self):
        if self._file_cancel_event is not None:
            self._file_cancel_event.set()
        self.file_cancel.setEnabled(False)

    def on_file_done(self, status, text):
        self.file_status.setText(status)
        self.file_bar.setVisible(False)
        self.file_pick.setEnabled(True)
        self.file_cancel.setEnabled(False)
        self._file_cancel_event = None
        if text:
            self.file_result.setPlainText(text)
        self.file_save.setEnabled(bool(text))

    def on_file_save(self):
        """Ergebnis als Datei speichern; Endung nach dem Format, in dem es
        transkribiert wurde (nicht nach der jetzigen Auswahl im Menü)."""
        text = self.file_result.toPlainText()
        if not text:
            return
        ext = FILE_EXT.get(self._file_result_fmt, ".txt")
        stem = os.path.splitext(os.path.basename(self._file_source))[0] or "transcript"
        folder = os.path.dirname(self._file_source) or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("file_save"), os.path.join(folder, stem + ext),
            "%s (*%s)" % (tr("file_format_" + self._file_result_fmt), ext))
        if not path:
            return
        try:
            # Atomar: eine vorhandene Datei ist danach ganz alt oder ganz neu.
            fsutil.atomic_write(path, text if text.endswith("\n") else text + "\n",
                                mode=0o600)
        except OSError:
            self.file_status.setText("✕ " + tr("file_save_failed"))
            return
        self.file_status.setText("✓ " + tr("file_saved", name=os.path.basename(path)))

    def on_file_copy(self):
        text = self.file_result.toPlainText()
        if text:
            clip_copy(text)
            self.file_status.setText("✓ " + tr("copied"))

    # ------------------------------------------------------- Update-Prüfung
    def on_check_update(self):
        self.update_status.setText(tr("update_checking"))
        updatecheck.check_async(__version__, self.bridge.update_done.emit)

    def on_update_done(self, result):
        latest = result.get("latest")
        if not latest:
            self.update_status.setText(tr("update_failed"))
            self.update_get.setVisible(False)
            return
        if result.get("update_available"):
            self.update_status.setText(
                tr("update_available", latest=latest, current=__version__))
            self.update_get.setVisible(True)
        else:
            self.update_status.setText(tr("update_none", current=__version__))
            self.update_get.setVisible(False)

    # ---------------------------------------------------- Diagnose kopieren
    def on_diag(self, target, run_async=True):
        """Bericht im Hintergrund sammeln (journalctl und die Server-Probe
        haben Fristen von Sekunden) und danach im GUI-Thread ausliefern."""
        self.diag_copy.setEnabled(False)
        self.diag_save.setEnabled(False)
        self.diag_status.setText(tr("diag_collecting"))

        def run():
            self.bridge.diag_done.emit(target, diagnostics.build_report())
        if run_async:
            threading.Thread(target=run, daemon=True).start()
        else:
            run()

    def on_diag_done(self, target, report):
        self.diag_copy.setEnabled(True)
        self.diag_save.setEnabled(True)
        if target == "save":
            self._diag_save(report)
            return
        # Qt-Zwischenablage des Kontrollzentrums, nicht die Diktat-Einfügewege.
        QApplication.clipboard().setText(report)
        self.diag_status.setText(tr("diag_copied", lines=report.count("\n")))

    def _diag_save(self, report):
        path, _ = QFileDialog.getSaveFileName(
            self, tr("diag_save"),
            os.path.join(os.path.expanduser("~"), diagnostics.default_filename()),
            "Text (*.txt)")
        if not path:
            self.diag_status.setText("")
            return
        try:
            diagnostics.save_report(path, report)
        except OSError:
            self.diag_status.setText(tr("diag_save_failed"))
            return
        self.diag_status.setText(tr("diag_saved", name=os.path.basename(path)))

    # ---------------------------------------------------- Zurücksetzen (#31)
    def on_reset_defaults(self):
        config.reset_defaults()
        self.cfg.reload(force=True)
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        QTimer.singleShot(0, self._rebuild_ui)
        QTimer.singleShot(10, lambda: self._toast_reset())

    def _toast_reset(self):
        # nach dem Neuaufbau die Bestätigung in der System-Seite anzeigen
        if hasattr(self, "reset_status"):
            self.reset_status.setText("✓ " + tr("reset_done"))


class _StatsChart(QWidget):
    """Schlankes Balkendiagramm: Wörter pro Tag (letzte N Tage). Heutiger Tag
    hervorgehoben in der Markenfarbe; passt sich hell/dunkel über die Palette an."""

    def __init__(self, accent):
        super().__init__()
        self._series = []
        self._accent = QColor(accent)
        self.setMinimumHeight(170)

    def set_series(self, series):
        self._series = list(series or [])
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 10, 10, 14, 22
        plot_h = h - pad_t - pad_b
        plot_w = w - pad_l - pad_r
        series = self._series
        if not series or plot_w <= 0 or plot_h <= 0:
            return
        mx = max((v for _, v in series), default=0)
        base_y = pad_t + plot_h
        axis = self.palette().color(QPalette.WindowText)
        axis.setAlpha(60)
        p.setPen(axis)
        p.drawLine(pad_l, base_y, w - pad_r, base_y)
        n = len(series)
        gap = 4
        bw = max(2.0, (plot_w - gap * (n - 1)) / n)
        muted = QColor(self._accent)
        muted.setAlpha(95)
        p.setPen(Qt.NoPen)
        for i, (_, v) in enumerate(series):
            x = pad_l + i * (bw + gap)
            bh = 0 if mx <= 0 else (v / mx) * plot_h
            if bh < 2:
                continue
            p.setBrush(self._accent if i == n - 1 else muted)
            p.drawRoundedRect(int(x), int(base_y - bh), int(bw), int(bh), 2, 2)
        label = self.palette().color(QPalette.WindowText)
        label.setAlpha(140)
        p.setPen(label)
        if mx > 0:
            p.drawText(pad_l, pad_t - 1, f"{mx}")
        p.drawText(pad_l, h - 5, series[0][0][5:])               # ältester Tag MM-DD
        p.drawText(w - pad_r - 34, h - 5, series[-1][0][5:])     # heute MM-DD


PRIVACY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_%s"


def permission_status():
    """Rechte, die Quassel braucht, je Plattform: [(Textschlüssel, Status,
    Einstellungs-URL oder None)], Status "ok" | "missing" | "unknown".
    Nur Statusabfragen (Preflight), kein Rechte-Dialog wird ausgelöst."""
    if IS_WINDOWS:
        return []
    if IS_MAC:
        mic = {"granted": "ok", "denied": "missing"}.get(mac_mic_permission(), "unknown")
        listen = post = None
        try:
            import Quartz
            if hasattr(Quartz, "CGPreflightListenEventAccess"):
                listen = bool(Quartz.CGPreflightListenEventAccess())
            if hasattr(Quartz, "CGPreflightPostEventAccess"):
                post = bool(Quartz.CGPreflightPostEventAccess())
        except ImportError:
            pass

        def st(v):
            return "unknown" if v is None else ("ok" if v else "missing")
        return [("ob_perm_mic", mic, PRIVACY_URL % "Microphone"),
                ("ob_perm_input", st(listen), PRIVACY_URL % "ListenEvent"),
                ("ob_perm_access", st(post), PRIVACY_URL % "Accessibility")]
    try:
        import grp
        member = grp.getgrnam("input").gr_gid in os.getgroups()
        status = "ok" if member else "missing"
    except (ImportError, KeyError):
        status = "unknown"
    return [("ob_perm_input_linux", status, None)]


def model_status():
    """("ok", Name) | ("missing", Name) | ("none", "") für das eingestellte
    Sprachmodell (server.env)."""
    path = config.read_serverenv().get("MODEL_PATH", "")
    if not path:
        return "none", ""
    name = os.path.basename(path)
    return ("ok" if os.path.exists(path) else "missing"), name


def clip_level(data):
    """Lautstärke einer Aufnahme (s16le) auf der Skala von audio.rms_level."""
    import array
    samples = array.array("h")
    samples.frombytes(data[:len(data) - len(data) % 2])
    if not samples:
        return 0.0
    rms = (sum(x * x for x in samples) / len(samples)) ** 0.5
    return min(rms / 8000.0, 1.0)


class OnboardingDialog(QDialog):
    """Erst-Einrichtung (#27, G110) in kurzen Schritten: Begrüßung mit Hotkey,
    Rechte (nur anzeigen, was fehlt, und wohin), Mikrofontest ohne Wiedergabe,
    Sprachmodell, „jetzt ausprobieren". Jeder Schritt ist überspringbar; der
    Dialog lässt sich jederzeit schließen.

    record_fn, permissions und models sind austauschbar (Tests): record_fn()
    liefert rohe PCM-Bytes oder wirft MicTestError."""

    mic_done = Signal(object)          # Bytes oder Fehlertext

    def __init__(self, parent, chord_label, record_fn=None, permissions=None,
                 models=None):
        super().__init__(parent)
        self.open_settings = False      # Wake-Word in den Einstellungen zeigen
        self.open_page = None           # "wake" | "speech"
        self.record_fn = record_fn
        self.chord_label = chord_label
        self._permissions = permissions or permission_status
        self._models = models or model_status
        self.setWindowTitle(tr("ob_title"))
        self.setModal(True)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        head.addWidget(logo)
        self.title = QLabel(tr("ob_title"))
        self.title.setStyleSheet("font-size: 18px; font-weight: 800;")
        head.addWidget(self.title)
        head.addStretch(1)
        self.step_lbl = QLabel()
        self.step_lbl.setObjectName("desc")
        head.addWidget(self.step_lbl)
        lay.addLayout(head)

        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)
        self._titles = []
        for title_key, build in (("ob_title", self._page_welcome),
                                 ("ob_perm_title", self._page_permissions),
                                 ("ob_mic_title", self._page_mic),
                                 ("ob_model_title", self._page_model),
                                 ("ob_try_title", self._page_try)):
            self._titles.append(tr(title_key))
            self.pages.addWidget(build())

        buttons = QHBoxLayout()
        self.close_btn = QPushButton(tr("ob_close"))
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn)
        buttons.addStretch(1)
        self.back_btn = QPushButton(tr("ob_back"))
        self.back_btn.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        buttons.addWidget(self.back_btn)
        self.next_btn = QPushButton(tr("ob_next"))
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._next)
        buttons.addWidget(self.next_btn)
        lay.addLayout(buttons)
        self.mic_done.connect(self._on_mic_done)
        self.go(0)

    # ---------------------------------------------------------- Seiten
    def _text(self, text, layout, desc=False):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        if desc:
            lbl.setObjectName("desc")
        layout.addWidget(lbl)
        return lbl

    def _page_welcome(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self._text(tr("ob_body", chord=self.chord_label), v)
        self._text(tr("ob_wake"), v, desc=True)
        wake_btn = QPushButton(tr("ob_open_settings"))
        wake_btn.clicked.connect(self._go_settings)
        v.addWidget(wake_btn, 0, Qt.AlignLeft)
        v.addStretch(1)
        return w

    def _page_permissions(self):
        w = QWidget()
        v = QVBoxLayout(w)
        rows = self._permissions()
        self.perm_rows = {}
        if not rows:
            self._text(tr("ob_perm_none"), v)
        else:
            self._text(tr("ob_perm_hint"), v, desc=True)
        for key, status, url in rows:
            # Name über der Zeile (darf umbrechen), darunter Status und Knopf:
            # nebeneinander würden lange Rechtenamen abgeschnitten.
            name = QLabel(tr(key))
            name.setWordWrap(True)
            name.setStyleSheet("font-weight: 600;")
            v.addWidget(name)
            row = QHBoxLayout()
            state = QLabel(("✓ " if status == "ok" else "") + tr("ob_perm_" + (
                "ok" if status == "ok" else "missing" if status == "missing" else "unknown")))
            state.setAccessibleName(tr(key) + ": " + state.text())
            row.addWidget(state)
            row.addStretch(1)
            btn = None
            if url and status != "ok":
                btn = QPushButton(tr("ob_perm_open"))
                btn.setAccessibleName(tr("ob_perm_open") + ": " + tr(key))
                btn.clicked.connect(lambda _c=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                row.addWidget(btn)
            v.addLayout(row)
            self.perm_rows[key] = (status, state, btn)
        if IS_MAC and any(st != "ok" for _k, st, _u in rows):
            self._text(tr("ob_perm_restart"), v, desc=True)
        v.addStretch(1)
        return w

    def _page_mic(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self._text(tr("ob_mic_hint"), v, desc=True)
        self.mic_btn = QPushButton(tr("ob_mic_button"))
        self.mic_btn.clicked.connect(self.start_mic_test)
        self.mic_btn.setEnabled(self.record_fn is not None)
        v.addWidget(self.mic_btn, 0, Qt.AlignLeft)
        self.mic_bar = QProgressBar()
        self.mic_bar.setRange(0, 100)
        self.mic_bar.setValue(0)
        self.mic_bar.setTextVisible(False)
        self.mic_bar.setAccessibleName(tr("ob_mic_level"))
        v.addWidget(self.mic_bar)
        self.mic_result = self._text("", v)
        v.addStretch(1)
        return w

    def _page_model(self):
        w = QWidget()
        v = QVBoxLayout(w)
        state, name = self._models()
        if state == "ok":
            text = "✓ " + tr("ob_model_ok", model=name)
        elif state == "missing":
            text = tr("err_model_missing", model=name)
        else:
            text = tr("ob_model_none")
        self.model_lbl = self._text(text, v)
        self.model_btn = None
        if state != "ok":
            self.model_btn = QPushButton(tr("ob_model_open"))
            self.model_btn.clicked.connect(self._go_speech)
            v.addWidget(self.model_btn, 0, Qt.AlignLeft)
        v.addStretch(1)
        return w

    def _page_try(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self._text(tr("ob_try_hint", chord=self.chord_label), v)
        self.try_field = QPlainTextEdit()
        self.try_field.setAccessibleName(tr("ob_try_field"))
        self.try_field.setPlaceholderText(tr("ob_try_field"))
        v.addWidget(self.try_field, 1)
        return w

    # -------------------------------------------------------- Steuerung
    def go(self, index):
        index = max(0, min(self.pages.count() - 1, index))
        self.pages.setCurrentIndex(index)
        self.title.setText(self._titles[index])
        self.step_lbl.setText(tr("ob_step", n=index + 1, total=self.pages.count()))
        self.back_btn.setEnabled(index > 0)
        last = index == self.pages.count() - 1
        self.next_btn.setText(tr("ob_finish") if last else tr("ob_next"))
        if last:
            self.try_field.setFocus()

    def _next(self):
        if self.pages.currentIndex() == self.pages.count() - 1:
            self.accept()
        else:
            self.go(self.pages.currentIndex() + 1)

    def start_mic_test(self, run_async=True):
        """Aufnahme im Hintergrund; das Ergebnis kommt über mic_done zurück.
        Kein Abspielen, keine Transkription, nichts wird gespeichert."""
        self.mic_btn.setEnabled(False)
        self.mic_result.setText(tr("mic_testing"))

        def work():
            try:
                self.mic_done.emit(self.record_fn() or b"")
            except MicTestError as e:
                self.mic_done.emit(e.args[0])
            except Exception:  # noqa: BLE001 — Audio-Backend kaputt
                self.mic_done.emit(tr("err_mic_open"))
        if run_async:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def _on_mic_done(self, result):
        self.mic_btn.setEnabled(True)
        if isinstance(result, str):
            self.mic_bar.setValue(0)
            self.mic_result.setText("✕ " + result)
            return
        level = clip_level(result)
        self.mic_bar.setValue(int(round(100 * LevelMeter.to_frac(level))))
        if len(result) < 8000 or level <= LevelMeter.SILENT_LEVEL:
            self.mic_result.setText("✕ " + tr("mic_silent"))
        else:
            self.mic_result.setText("✓ " + tr("ob_mic_ok"))

    def _go_settings(self):
        self.open_settings = True
        self.open_page = "wake"
        self.accept()

    def _go_speech(self):
        self.open_page = "speech"
        self.accept()


def main():
    app = QApplication([])
    app.setApplicationName("Quassel")
    app.setDesktopFileName("quassel")
    app.setWindowIcon(app_icon())
    win = Center()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
