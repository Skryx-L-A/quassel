"""Quassel-Kontrollzentrum (Qt/PySide6) — klassisches Einstellungsfenster mit
Seitenleisten-Sektionen (Allgemein, Spracherkennung, Wörterbuch, Verlauf,
System), bewusst einfach und erweiterbar (neue Seite = neuer Eintrag in der
build()-Liste + eigene page_*-Methode).

Nur eine Fernbedienung: Fenster schließen beendet Quassel nicht.
Einstellungen wirken sofort (der Daemon liest die Config live).
"""
import os
import subprocess
import threading
import time
import wave

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from . import (__version__, ai, aimodes, config, i18n, stats, transcribe_file,
               updatecheck, whisperclient)
from .audio import RATE
from .config import MODEL_URL, MODELS
from .i18n import tr
from .net import download as net_download
if os.name == "nt":
    from .win.audio_win import list_mics
    from .win.paste import clip_copy
else:
    from .audio import list_mics, record_command
    from .platform_linux import clip_copy

UNITS_START = ["quasseld", "quassel-server", "quassel-pill"]
UNITS_STOP = ["quasseld", "quassel-server", "quassel-ydotoold"]
ICON_PATHS = [
    os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/quassel-voice.svg"),
    os.path.join(os.path.dirname(__file__), "..", "assets", "quassel.svg"),
]

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
QLabel#desc {{ color: palette(placeholder-text); }}
"""


IS_WINDOWS = os.name == "nt"


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def sysctl(*args):
    if IS_WINDOWS:
        class _R:  # auf Windows gibt es kein systemd; Tray-App steuert alles
            returncode = 1
        return _R()
    return subprocess.run(["systemctl", "--user", *args], check=False,
                          capture_output=True)


def daemon_active():
    if IS_WINDOWS:
        return True     # das Fenster läuft im Tray-App-Prozess selbst
    return sysctl("is-active", "--quiet", "quasseld").returncode == 0


def autostart_enabled():
    if IS_WINDOWS:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "Quassel")
            return True
        except OSError:
            return False
    return sysctl("is-enabled", "--quiet", "quasseld").returncode == 0


def autostart_set(on):
    """Windows: Run-Key in der Registry (gleicher Wert wie im Inno-Setup)."""
    import sys
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
    else:
        sysctl("try-restart", "quassel-server")


def app_icon():
    for p in ICON_PATHS:
        if os.path.exists(p):
            return QIcon(p)
    return QIcon.fromTheme("quassel-voice")


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
    update_done = Signal(dict)        # updatecheck.check()-Resultat
    ai_models = Signal(dict)          # {"available":bool,"models":[...]}
    ai_test_done = Signal(str)        # Ergebnis des KI-Tests


class Center(QMainWindow):
    def __init__(self, controller=None):
        """controller: unter Windows die Tray-App (WinApp) im selben Prozess —
        An/Aus läuft dann über sie statt über systemd."""
        super().__init__()
        self.controller = controller
        self.cfg = config.Cfg()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        self.bridge = Bridge()
        self.bridge.progress.connect(self.on_progress)
        self.bridge.message.connect(self.on_message)
        self.bridge.model_done.connect(self.on_model_done)
        self.bridge.file_done.connect(self.on_file_done)
        self.bridge.update_done.connect(self.on_update_done)
        self.bridge.ai_models.connect(self.on_ai_models)
        self.bridge.ai_test_done.connect(self.on_ai_test_done)
        self.nowheel = NoWheel(self)
        self._loading = True
        self.build()
        self._loading = False
        self.refresh_status()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh_status)
        timer.start(2000)
        QTimer.singleShot(0, self._maybe_onboard)
        if self.cfg.update_check:
            QTimer.singleShot(1500, lambda: updatecheck.check_async(
                __version__, self.bridge.update_done.emit))

    # ------------------------------------------------------------- Hilfen
    def guard(self, w):
        """Mausrad-Schutz + sauberer Fokus für Dropdowns/Slider."""
        w.setFocusPolicy(Qt.StrongFocus)
        w.installEventFilter(self.nowheel)
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
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addWidget(widget, 1)
        layout.addLayout(row)

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
        self.setStyleSheet(STYLE.format(
            accent=self._accent, accent_text=self._accent_text,
            accent_soft=accent_soft))

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
        self.stack = QStackedWidget()
        lv.addWidget(self.sidebar, 1)
        outer.addWidget(left)
        outer.addWidget(self.stack, 1)

        for key, builder in [("nav_general", self.page_general),
                             ("nav_speech", self.page_speech),
                             ("nav_dict", self.page_dict),
                             ("nav_replace", self.page_replace),
                             ("nav_file", self.page_file),
                             ("nav_ai", self.page_ai),
                             ("nav_history", self.page_history),
                             ("nav_stats", self.page_stats),
                             ("nav_system", self.page_system)]:
            QListWidgetItem(tr(key), self.sidebar)
            self.stack.addWidget(builder())
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self.setCentralWidget(root)

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
            f"QPushButton:hover{{background:{self._accent_hover};}}")
        self.toggle_btn.clicked.connect(self.on_toggle)
        if IS_WINDOWS and self.controller is None:
            self.toggle_btn.hide()      # ohne Tray-App nichts zu schalten
        row.addWidget(self.toggle_btn)
        g.addLayout(row)
        self.hint = self.desc(tr("hint", chord=self.chord_label()), g)

        g = self.group(tr("sec_pill"), lay)
        self.pill_show = QCheckBox(tr("pill_show"))
        self.pill_show.setChecked(self.cfg.pill_enabled)
        self.pill_show.toggled.connect(self.save_settings)
        g.addWidget(self.pill_show)
        self.pill_size = self.guard(QSlider(Qt.Horizontal))
        self.pill_size.setRange(60, 200)
        self.pill_size.setValue(int(self.cfg.pill_scale * 100))
        self.pill_size.valueChanged.connect(self.save_settings)
        self.labeled_row(tr("pill_size"), self.pill_size, g)
        self.pill_opacity = self.guard(QSlider(Qt.Horizontal))
        self.pill_opacity.setRange(15, 100)
        self.pill_opacity.setValue(int(self.cfg.pill_opacity * 100))
        self.pill_opacity.valueChanged.connect(self.save_settings)
        self.labeled_row(tr("pill_opacity"), self.pill_opacity, g)
        self.pill_preview = QCheckBox(tr("pill_preview"))
        self.pill_preview.setChecked(self.cfg.pill_preview)
        self.pill_preview.toggled.connect(self.save_settings)
        g.addWidget(self.pill_preview)

        g = self.group(tr("sec_hotkey"), lay)
        self.chord = self.guard(QComboBox())
        for key in config.CHORDS:
            self.chord.addItem(tr(config.CHORD_LABEL_KEYS[key]), key)
        self.chord.setCurrentIndex(list(config.CHORDS).index(self.cfg.chord))
        self.chord.currentIndexChanged.connect(self.save_settings)
        g.addWidget(self.chord)

        g = self.group(tr("sec_dictating"), lay)
        self.mute_combo = self.guard(QComboBox())
        for val, key in (("off", "mute_off"), ("music", "mute_music"), ("all", "mute_all")):
            self.mute_combo.addItem(tr(key), val)
        self.mute_combo.setCurrentIndex({"off": 0, "music": 1, "all": 2}.get(self.cfg.mute_mode, 0))
        self.mute_combo.currentIndexChanged.connect(self.save_settings)
        self.labeled_row(tr("mute_label"), self.mute_combo, g)
        self.desc(tr("mute_hint"), g)

        lay.addStretch(1)
        note = QLabel(tr("close_note"))
        note.setObjectName("desc")
        note.setAlignment(Qt.AlignCenter)
        lay.addWidget(note)
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

        # --- Wake-Word (Freisprechen, opt-in) ---
        g = self.group(tr("sec_wakeword"), lay)
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

        g = self.group(tr("model"), lay)
        self.model = self.guard(QComboBox())
        env = config.read_serverenv()
        current = next((m for m in MODELS if f"ggml-{m}.bin" in env.get("MODEL_PATH", "")),
                       "small")
        for m in MODELS:
            self.model.addItem(m, m)
        self.model.setCurrentIndex(MODELS.index(current))
        self.model.currentIndexChanged.connect(self.on_model)
        g.addWidget(self.model)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        g.addWidget(self.progress)

        g = self.group(tr("sec_mic"), lay)
        self.mic = self.guard(QComboBox())
        self.mic.addItem(tr("mic_default"), "default")
        for name, desc in list_mics():
            self.mic.addItem(desc, name)
            if name == self.cfg.mic:
                self.mic.setCurrentIndex(self.mic.count() - 1)
        self.mic.currentIndexChanged.connect(self.save_settings)
        g.addWidget(self.mic)
        test = QPushButton(tr("mic_test"))
        test.clicked.connect(self.on_mictest)
        g.addWidget(test)
        self.test_out = QLabel()
        self.test_out.setWordWrap(True)
        g.addWidget(self.test_out)

        lay.addStretch(1)
        return scroll

    # -------------------------------------------------- Seite: Wörterbuch
    def page_dict(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_dict"), lay)
        self.desc(tr("dict_hint"), g)
        self.dict_edit = QPlainTextEdit("\n".join(config.dictionary_words()))
        self.dict_timer = QTimer(self)
        self.dict_timer.setSingleShot(True)
        self.dict_timer.timeout.connect(
            lambda: config.dictionary_save(self.dict_edit.toPlainText()))
        self.dict_edit.textChanged.connect(lambda: self.dict_timer.start(800))
        g.addWidget(self.dict_edit)
        self.auto_learn = QCheckBox(tr("auto_learn"))
        self.auto_learn.setChecked(self.cfg.auto_learn)
        self.auto_learn.toggled.connect(self.save_settings)
        g.addWidget(self.auto_learn)
        self.desc(tr("auto_learn_hint"), g)
        return scroll

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
        g.addWidget(self.replace_edit)
        return scroll

    # --------------------------------------------- Seite: Datei transkribieren
    def page_file(self):
        scroll, lay = self.page()
        g = self.group(tr("sec_file"), lay)
        self.desc(tr("file_hint"), g)
        pick = QPushButton(tr("file_pick"))
        pick.clicked.connect(self.on_pick_file)
        g.addWidget(pick)
        self.file_status = QLabel()
        self.file_status.setObjectName("desc")
        self.file_status.setWordWrap(True)
        g.addWidget(self.file_status)
        self.file_result = QPlainTextEdit()
        self.file_result.setReadOnly(True)
        g.addWidget(self.file_result)
        copy = QPushButton(tr("copy"))
        copy.clicked.connect(lambda: clip_copy(self.file_result.toPlainText()))
        g.addWidget(copy)
        lay.addStretch(1)
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
        mrow.addWidget(QLabel(tr("ai_model")))
        self.ai_model = self.guard(QComboBox())
        self.ai_model.setEditable(True)
        if self.cfg.ai_model:
            self.ai_model.addItem(self.cfg.ai_model, self.cfg.ai_model)
        self.ai_model.currentTextChanged.connect(self.save_settings)
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

        g = self.group(tr("sec_ai_modes"), lay)
        self.desc(tr("ai_modes_hint"), g)
        self.ai_modes_edit = QPlainTextEdit(config.ai_modes_text())
        self.ai_modes_timer = QTimer(self)
        self.ai_modes_timer.setSingleShot(True)
        self.ai_modes_timer.timeout.connect(self._save_ai_modes)
        self.ai_modes_edit.textChanged.connect(lambda: self.ai_modes_timer.start(800))
        g.addWidget(self.ai_modes_edit)
        lay.addStretch(1)
        if self.cfg.ai_enabled:
            QTimer.singleShot(200, self.on_ai_refresh)
        return scroll

    def _save_ai_modes(self):
        config.ai_modes_save(self.ai_modes_edit.toPlainText())
        cur = self.ai_mode.currentData()
        self._fill_ai_modes(self.ai_mode)
        self.select_data(self.ai_mode, cur)

    def on_ai_refresh(self):
        self.ai_status.setText(tr("ai_testing"))
        ep = self.ai_endpoint.text().strip() or config.OLLAMA_DEFAULT

        def run():
            avail = ai.available(ep)
            models = ai.list_models(ep) if avail else []
            self.bridge.ai_models.emit({"available": avail, "models": models})
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
        self.ai_test_lbl.setText(tr("ai_testing"))
        model = self.ai_model.currentText().strip()
        ep = self.ai_endpoint.text().strip() or config.OLLAMA_DEFAULT
        timeout = self.cfg.ai_timeout
        sample = "um so like we should uh ship it on friday yeah"
        system = aimodes.system_for("cleanup")

        def run():
            out = ai.generate(sample, model=model, system=system,
                              endpoint=ep, timeout=timeout)
            self.bridge.ai_test_done.emit(out or "")
        threading.Thread(target=run, daemon=True).start()

    def on_ai_test_done(self, out):
        if out:
            self.ai_test_lbl.setText("✓ " + tr("ai_test_ok", text=out[:120]))
        else:
            self.ai_test_lbl.setText("✕ " + tr("ai_test_fail"))

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
            sysctl("stop", *UNITS_STOP)
        else:
            sysctl("start", *UNITS_START)
        self.refresh_status()

    # ----------------------------------------------------------- Speichern
    def save_settings(self, *_a):
        if self._loading:
            return
        config.save({
            ("pill", "enabled"): str(self.pill_show.isChecked()).lower(),
            ("pill", "scale"): self.pill_size.value() / 100,
            ("pill", "opacity"): self.pill_opacity.value() / 100,
            ("pill", "show_preview"): str(self.pill_preview.isChecked()).lower(),
            ("hotkey", "chord"): self.chord.currentData(),
            ("behavior", "mute_while_dictating"): self.mute_combo.currentData(),
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
            ("ai", "endpoint"): self.ai_endpoint.text().strip() or config.OLLAMA_DEFAULT,
            ("ai", "model"): self.ai_model.currentText().strip(),
            ("ai", "post_process"): str(self.ai_post.isChecked()).lower(),
            ("ai", "post_mode"): self.ai_mode.currentData() or "cleanup",
            ("ai", "voice_modes"): str(self.ai_voice.isChecked()).lower(),
            ("ui", "language"): self.uilang.currentData(),
        })
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
    def _maybe_onboard(self):
        if self.cfg.onboarded:
            return
        dlg = OnboardingDialog(self, self.chord_label())
        dlg.exec()
        if dlg.open_settings:
            self.sidebar.setCurrentRow(1)       # Spracherkennung (Wake-Word)
        config.save({("system", "onboarded"): "true"})
        self.cfg.reload(force=True)

    def on_autostart(self, on):
        if IS_WINDOWS:
            autostart_set(on)
        else:
            sysctl("enable" if on else "disable", "quasseld")

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
        modeldir = os.path.dirname(env.get("MODEL_PATH", "")) or \
            os.path.join(config.DATADIR, "models")
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
                return None
            time.sleep(2.5)
            rec.stop()
            return rec.raw_bytes()
        cmd = record_command(self.cfg.mic)
        if cmd is None:
            return None
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

    def on_mictest(self):
        self.test_out.setText(tr("mic_testing"))

        def run():
            try:
                data = self._mictest_record()
            except Exception:  # noqa: BLE001 — Audio-Backend kaputt
                data = None
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
            if not whisperclient.ensure_server():
                os.unlink(wavpath)
                self.bridge.message.emit("✕ " + tr("no_server"))
                return
            text = whisperclient.transcribe(wavpath, self.cfg, timeout=60)
            os.unlink(wavpath)
            if text is None:
                self.bridge.message.emit("✕ " + tr("no_server"))
                return
            text = " ".join(text.split()).strip() or "—"
            self.bridge.message.emit("✓ " + tr("mic_result", text=text))
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------ Datei transkribieren (#21)
    def on_pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("file_pick"))
        if not path:
            return
        if not transcribe_file.is_supported(path):
            self.file_status.setText("✕ " + tr("file_failed"))
            return
        if not transcribe_file.have_ffmpeg() and not path.lower().endswith(".wav"):
            self.file_status.setText("✕ " + tr("file_no_ffmpeg"))
            return
        name = os.path.basename(path)
        self.file_status.setText(tr("file_running", name=name))
        self.file_result.setPlainText("")

        def run():
            try:
                text = transcribe_file.transcribe_file(path, self.cfg)
            except Exception:    # noqa: BLE001
                text = None
            if not text or not text.strip():
                self.bridge.file_done.emit("✕ " + tr("file_failed"), "")
                return
            text = text.strip()
            self.bridge.file_done.emit(
                "✓ " + tr("file_done", chars=len(text)), text)
        threading.Thread(target=run, daemon=True).start()

    def on_file_done(self, status, text):
        self.file_status.setText(status)
        if text:
            self.file_result.setPlainText(text)
            clip_copy(text)

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


class OnboardingDialog(QDialog):
    """Erst-Start-Begrüßung (#27): erklärt den Hotkey und „nichts einzustellen,
    einfach schließen", plus den Hinweis, dass das Wake-Word änderbar ist."""

    def __init__(self, parent, chord_label):
        super().__init__(parent)
        self.open_settings = False
        self.setWindowTitle(tr("ob_title"))
        self.setModal(True)
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        head.addWidget(logo)
        title = QLabel(tr("ob_title"))
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        body = QLabel(tr("ob_body", chord=chord_label))
        body.setWordWrap(True)
        lay.addWidget(body)
        wake = QLabel(tr("ob_wake"))
        wake.setWordWrap(True)
        wake.setObjectName("desc")
        lay.addWidget(wake)
        lay.addStretch(1)

        buttons = QDialogButtonBox()
        settings_btn = buttons.addButton(tr("ob_open_settings"),
                                         QDialogButtonBox.ActionRole)
        close_btn = buttons.addButton(tr("ob_close"), QDialogButtonBox.AcceptRole)
        settings_btn.clicked.connect(self._go_settings)
        close_btn.clicked.connect(self.accept)
        lay.addWidget(buttons)

    def _go_settings(self):
        self.open_settings = True
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
