"""VoxType-Kontrollzentrum (Qt/PySide6) — alle Einstellungen an einem Ort,
bewusst einfach gehalten (Vorbild Wispr Flow).

Nur eine Fernbedienung: Fenster schließen beendet VoxType nicht.
Einstellungen wirken sofort (der Daemon liest die Config live).
"""
import os
import subprocess
import threading
import time
import urllib.request
import wave

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from . import config, i18n
from .audio import RATE, list_mics, record_command
from .config import MODEL_URL, MODELS
from .i18n import tr
from .platform_linux import clip_copy
from .whisperclient import SERVER

UNITS_START = ["voxtyped", "voxtype-server"]
UNITS_STOP = ["voxtyped", "voxtype-server", "voxtype-pill", "voxtype-ydotoold"]


def sysctl(*args):
    return subprocess.run(["systemctl", "--user", *args], check=False,
                          capture_output=True)


def daemon_active():
    return sysctl("is-active", "--quiet", "voxtyped").returncode == 0


def autostart_enabled():
    return sysctl("is-enabled", "--quiet", "voxtyped").returncode == 0


class Bridge(QObject):
    """Thread → GUI-Signale."""
    progress = Signal(float)
    message = Signal(str)
    model_done = Signal(str)


class Center(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = config.Cfg()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        self.bridge = Bridge()
        self.bridge.progress.connect(self.on_progress)
        self.bridge.message.connect(self.on_message)
        self.bridge.model_done.connect(self.on_model_done)
        self._loading = True
        self.build()
        self._loading = False
        self.refresh_status()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh_status)
        timer.start(2000)

    # ----------------------------------------------------------------- Aufbau
    def build(self):
        self.setWindowTitle(tr("app_name"))
        self.setMinimumWidth(480)
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 12)
        outer.setSpacing(12)

        # Kopf: Status + großer Schalter
        head = QHBoxLayout()
        self.status_lbl = QLabel()
        self.status_lbl.setStyleSheet("font-size: 17px; font-weight: 600;")
        head.addWidget(self.status_lbl)
        head.addStretch(1)
        self.toggle_btn = QPushButton()
        self.toggle_btn.setMinimumHeight(36)
        self.toggle_btn.setStyleSheet("font-weight: 600; padding: 4px 22px;")
        self.toggle_btn.clicked.connect(self.on_toggle)
        head.addWidget(self.toggle_btn)
        outer.addLayout(head)

        hint = QLabel(tr("hint", chord=self.chord_label()))
        hint.setStyleSheet("color: gray;")
        outer.addWidget(hint)
        self.hint = hint

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(10)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # --- Pille (ganz oben, wie gewünscht) ---
        box, form = self.section(tr("sec_pill"))
        self.pill_show = QCheckBox(tr("pill_show"))
        self.pill_show.setChecked(self.cfg.pill_enabled)
        self.pill_show.toggled.connect(self.save_settings)
        form.addWidget(self.pill_show)
        form.addLayout(self.slider_row(tr("pill_size"), "pill_size",
                                       60, 200, int(self.cfg.pill_scale * 100)))
        form.addLayout(self.slider_row(tr("pill_opacity"), "pill_opacity",
                                       15, 100, int(self.cfg.pill_opacity * 100)))
        lay.addWidget(box)

        # --- Hotkey ---
        box, form = self.section(tr("sec_hotkey"))
        self.chord = QComboBox()
        for key in config.CHORDS:
            self.chord.addItem(tr(config.CHORD_LABEL_KEYS[key]), key)
        self.chord.setCurrentIndex(list(config.CHORDS).index(self.cfg.chord))
        self.chord.currentIndexChanged.connect(self.save_settings)
        form.addWidget(self.chord)
        lay.addWidget(box)

        # --- Spracherkennung ---
        box, form = self.section(tr("sec_speech"))
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("language")))
        self.lang = QComboBox()
        for val, key in (("auto", "lang_auto"), ("de", "lang_de"), ("en", "lang_en")):
            self.lang.addItem(tr(key), val)
        self.lang.setCurrentIndex({"auto": 0, "de": 1, "en": 2}.get(self.cfg.language, 0))
        self.lang.currentIndexChanged.connect(self.save_settings)
        row.addWidget(self.lang, 1)
        form.addLayout(row)
        self.punct = QCheckBox(tr("punctuation"))
        self.punct.setChecked(self.cfg.punctuation)
        self.punct.toggled.connect(self.save_settings)
        form.addWidget(self.punct)
        self.cmds = QCheckBox(tr("commands"))
        self.cmds.setChecked(self.cfg.commands)
        self.cmds.toggled.connect(self.save_settings)
        form.addWidget(self.cmds)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("model")))
        self.model = QComboBox()
        env = config.read_serverenv()
        current = next((m for m in MODELS if f"ggml-{m}.bin" in env.get("MODEL_PATH", "")),
                       "small")
        for m in MODELS:
            self.model.addItem(m, m)
        self.model.setCurrentIndex(MODELS.index(current))
        self.model.currentIndexChanged.connect(self.on_model)
        row.addWidget(self.model, 1)
        form.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        form.addWidget(self.progress)
        lay.addWidget(box)

        # --- Mikrofon ---
        box, form = self.section(tr("sec_mic"))
        self.mic = QComboBox()
        self.mic.addItem(tr("mic_default"), "default")
        for name, desc in list_mics():
            self.mic.addItem(desc, name)
            if name == self.cfg.mic:
                self.mic.setCurrentIndex(self.mic.count() - 1)
        self.mic.currentIndexChanged.connect(self.save_settings)
        form.addWidget(self.mic)
        test = QPushButton("🎙  " + tr("mic_test"))
        test.clicked.connect(self.on_mictest)
        form.addWidget(test)
        self.test_out = QLabel()
        self.test_out.setWordWrap(True)
        form.addWidget(self.test_out)
        lay.addWidget(box)

        # --- Wörterbuch ---
        box, form = self.section(tr("sec_dict"))
        dh = QLabel(tr("dict_hint"))
        dh.setStyleSheet("color: gray;")
        dh.setWordWrap(True)
        form.addWidget(dh)
        self.dict_edit = QPlainTextEdit("\n".join(config.dictionary_words()))
        self.dict_edit.setMaximumHeight(110)
        self.dict_timer = QTimer(self)
        self.dict_timer.setSingleShot(True)
        self.dict_timer.timeout.connect(
            lambda: config.dictionary_save(self.dict_edit.toPlainText()))
        self.dict_edit.textChanged.connect(lambda: self.dict_timer.start(800))
        form.addWidget(self.dict_edit)
        lay.addWidget(box)

        # --- Verlauf ---
        box, form = self.section(tr("sec_history"))
        self.hist_enable = QCheckBox(tr("history_enable"))
        self.hist_enable.setChecked(self.cfg.history_enabled)
        self.hist_enable.toggled.connect(self.save_settings)
        form.addWidget(self.hist_enable)
        hh = QLabel(tr("history_copy"))
        hh.setStyleSheet("color: gray;")
        form.addWidget(hh)
        self.hist = QListWidget()
        self.hist.setMaximumHeight(140)
        self.hist.itemClicked.connect(self.on_hist_click)
        form.addWidget(self.hist)
        clear = QPushButton(tr("history_clear"))
        clear.clicked.connect(self.on_hist_clear)
        form.addWidget(clear)
        self.reload_history()
        lay.addWidget(box)

        # --- System ---
        box, form = self.section(tr("sec_system"))
        self.auto = QCheckBox(tr("autostart"))
        self.auto.setChecked(autostart_enabled())
        self.auto.toggled.connect(self.on_autostart)
        form.addWidget(self.auto)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("ui_language")))
        self.uilang = QComboBox()
        for val, label in (("auto", tr("ui_auto")), ("de", "Deutsch"), ("en", "English")):
            self.uilang.addItem(label, val)
        self.uilang.setCurrentIndex({"auto": 0, "de": 1, "en": 2}.get(self.cfg.ui_language, 0))
        self.uilang.currentIndexChanged.connect(self.save_settings)
        row.addWidget(self.uilang, 1)
        form.addLayout(row)
        lay.addWidget(box)

        note = QLabel(tr("close_note"))
        note.setStyleSheet("color: gray; font-size: 11px;")
        note.setAlignment(Qt.AlignCenter)
        outer.addWidget(note)
        self.setCentralWidget(root)

    def section(self, title):
        box = QGroupBox(title)
        form = QVBoxLayout(box)
        form.setSpacing(7)
        return box, form

    def slider_row(self, label, name, lo, hi, value):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(value)
        s.valueChanged.connect(self.save_settings)
        setattr(self, name, s)
        row.addWidget(s, 1)
        return row

    def chord_label(self):
        return tr(config.CHORD_LABEL_KEYS[self.cfg.chord]).split("(")[0].strip()

    # ----------------------------------------------------------------- Status
    def refresh_status(self):
        on = daemon_active()
        self.status_lbl.setText(("🟢 " + tr("on")) if on else ("⚪ " + tr("off")))
        self.toggle_btn.setText(tr("turn_off") if on else tr("turn_on"))

    def on_toggle(self):
        if daemon_active():
            sysctl("stop", *UNITS_STOP)
        else:
            sysctl("start", *UNITS_START)
        self.refresh_status()

    # ------------------------------------------------------------ Speichern
    def save_settings(self, *_a):
        if self._loading:
            return
        config.save({
            ("pill", "enabled"): str(self.pill_show.isChecked()).lower(),
            ("pill", "scale"): self.pill_size.value() / 100,
            ("pill", "opacity"): self.pill_opacity.value() / 100,
            ("hotkey", "chord"): self.chord.currentData(),
            ("speech", "language"): self.lang.currentData(),
            ("speech", "punctuation"): str(self.punct.isChecked()).lower(),
            ("speech", "commands"): str(self.cmds.isChecked()).lower(),
            ("speech", "mic"): self.mic.currentData(),
            ("history", "enabled"): str(self.hist_enable.isChecked()).lower(),
            ("ui", "language"): self.uilang.currentData(),
        })
        self.cfg.reload(force=True)
        self.hint.setText(tr("hint", chord=self.chord_label()))

    def on_autostart(self, on):
        sysctl("enable" if on else "disable", "voxtyped")

    # ------------------------------------------------------------- Verlauf
    def reload_history(self):
        self.hist.clear()
        for e in reversed(config.history_read()):
            text = e.get("text", "")
            self.hist.addItem(text if len(text) <= 90 else text[:89] + "…")
            self.hist.item(self.hist.count() - 1).setData(Qt.UserRole, text)

    def on_hist_click(self, item):
        clip_copy(item.data(Qt.UserRole))
        self.test_out.setText("✅ " + tr("copied"))

    def on_hist_clear(self):
        config.history_clear()
        self.reload_history()

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
            try:
                os.makedirs(modeldir, exist_ok=True)
                def hook(blocks, bs, total):
                    if total > 0:
                        self.bridge.progress.emit(min(blocks * bs / total, 1.0))
                urllib.request.urlretrieve(MODEL_URL.format(model), target, hook)
                self.bridge.model_done.emit(target + "|" + model)
            except Exception as e:  # noqa: BLE001
                self.bridge.message.emit("❌ " + tr("download_failed", err=str(e)[:80]))
        threading.Thread(target=download, daemon=True).start()

    def switch_model(self, path, model):
        env = config.read_serverenv()
        env["MODEL_PATH"] = path
        config.write_serverenv(env)
        sysctl("try-restart", "voxtype-server")
        self.progress.setVisible(False)
        self.test_out.setText("✅ " + tr("model_switched", model=model))

    def on_progress(self, frac):
        self.progress.setValue(int(frac * 100))

    def on_message(self, msg):
        self.progress.setVisible(False)
        self.test_out.setText(msg)

    def on_model_done(self, payload):
        path, model = payload.rsplit("|", 1)
        self.switch_model(path, model)

    # ------------------------------------------------------- Mikrofon-Test
    def on_mictest(self):
        self.test_out.setText(tr("mic_testing"))

        def run():
            cmd = record_command(self.cfg.mic)
            if cmd is None:
                self.bridge.message.emit("❌ pw-record/parecord fehlt")
                return
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
            if not data or len(data) < 8000:
                self.bridge.message.emit("❌ " + tr("mic_nothing"))
                return
            self.bridge.message.emit("⏳ " + tr("mic_transcribing"))
            wavpath = os.path.join(config.DATADIR, "mictest.wav")
            os.makedirs(config.DATADIR, exist_ok=True)
            with wave.open(wavpath, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(data)
            sysctl("start", "voxtype-server")
            for _ in range(120):
                if subprocess.run(["curl", "-fsS", "-m", "2", "-o", "/dev/null",
                                   SERVER + "/"], check=False).returncode == 0:
                    break
                time.sleep(0.5)
            r = subprocess.run(
                ["curl", "-fsS", "-m", "60", SERVER + "/inference",
                 "-F", f"file=@{wavpath}", "-F", "response_format=text"],
                capture_output=True, text=True, check=False)
            os.unlink(wavpath)
            if r.returncode != 0:
                self.bridge.message.emit("❌ " + tr("no_server"))
                return
            text = " ".join(r.stdout.split()).strip() or "—"
            self.bridge.message.emit("✅ " + tr("mic_result", text=text))
        threading.Thread(target=run, daemon=True).start()


def main():
    app = QApplication([])
    app.setApplicationName("VoxType")
    app.setDesktopFileName("voxtype")
    win = Center()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
