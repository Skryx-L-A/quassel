"""Bedienung der Beta-Funktionen und neuer Schalter im Kontrollzentrum
(offscreen, eigene config.ini): Auto-Stopp/VAD, Wake-Word-Erkenner,
Vorlaufpuffer, Datei-Transkription, Per-App-Profile."""
import configparser
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication              # noqa: E402

from quassel import center, config, i18n, vad          # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def c(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    # onboarded: sonst öffnete processEvents() den modalen Willkommensdialog
    config.save({("ui", "language"): "en", ("system", "onboarded"): "true"})
    win = center.Center()
    yield win
    win.close()
    i18n.set_language(None)


def saved():
    p = configparser.ConfigParser(interpolation=None)
    p.read(config.CONFIG)
    return p


def beta(c):
    return c.stack.widget(c._page_keys.index("nav_beta"))


# ------------------------------------------------------- (a) VAD / Auto-Stopp
def test_autostop_defaults_follow_vad_settings(c):
    assert c.vad_autostop.isChecked() is False
    assert c.vad_silence.value() == pytest.approx(vad.AUTOSTOP_SILENCE_DEFAULT)
    assert c.vad_backend.currentData() == "auto"
    assert c.vad_model.text() == ""
    assert not c.vad_silence.isEnabled()
    assert beta(c).isAncestorOf(c.vad_autostop)


def test_autostop_settings_round_trip_through_vad_settings(c, tmp_path, monkeypatch):
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(center.QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(model), ""))
    c.vad_autostop.setChecked(True)
    c.vad_silence.setValue(2.5)
    c.save_timer.timeout.emit()
    c.vad_backend.setCurrentIndex(c.vad_backend.findData("rms"))
    c.vad_model_pick.click()
    s = vad.settings()
    assert (s.autostop, s.autostop_silence, s.backend, s.silero_model) == \
        (True, 2.5, "rms", str(model))
    assert c.vad_silence.isEnabled()


def test_silence_range_matches_vad(c):
    assert (c.vad_silence.minimum(), c.vad_silence.maximum()) == vad.AUTOSTOP_SILENCE_RANGE


# ------------------------------------------------ (b) Wake-Word-Erkenner
def test_wake_spotter_defaults_follow_wake_settings(c):
    from quassel import wakeword
    ws = wakeword.wake_settings()
    assert c.wake_model.text() == ws.spotter_model == ""
    assert c.wake_threshold.value() == pytest.approx(ws.spotter_threshold)
    assert c.wake_verify.isChecked() is ws.verify is True
    assert beta(c).isAncestorOf(c.wake_model)


def test_wake_spotter_settings_round_trip(c, tmp_path, monkeypatch):
    from quassel import wakeword
    model = tmp_path / "hey_quassel.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(center.QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(model), ""))
    c.wake_model_pick.click()
    c.wake_threshold.setValue(0.7)
    c.save_timer.timeout.emit()
    c.wake_verify.setChecked(False)
    ws = wakeword.wake_settings()
    assert (ws.spotter_model, ws.spotter_threshold, ws.verify) == (str(model), 0.7, False)


def test_threshold_range_matches_wake_settings(c):
    assert (c.wake_threshold.minimum(), c.wake_threshold.maximum()) == (0.05, 0.99)


# ------------------------------------------------------ (c) Vorlaufpuffer
def test_preroll_is_off_by_default_and_saves_in_range(c):
    assert c.preroll.value() == 0 and c.preroll.text() == "Off"
    assert (c.preroll.minimum(), c.preroll.maximum()) == (0, 2000)
    c.preroll.setValue(300)
    c.save_timer.timeout.emit()
    assert saved().getint("speech", "preroll_ms") == 300
    c.cfg.reload(force=True)
    assert c.cfg.preroll_ms == 300
    assert beta(c).isAncestorOf(c.preroll)


@pytest.mark.parametrize("mac", [True, False])
def test_mac_microphone_indicator_hint(tmp_path, monkeypatch, qapp, mac):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(center, "IS_MAC", mac)
    monkeypatch.setattr(center, "IS_WINDOWS", False)
    monkeypatch.setattr(center, "autostart_enabled", lambda: False)
    monkeypatch.setattr(center, "daemon_active", lambda: True)
    monkeypatch.setattr(center.beep, "list_outputs", lambda: [], raising=False)
    config.save({("system", "onboarded"): "true"})
    win = center.Center(controller=SimpleNamespace(enabled=True))
    try:
        assert (win.preroll_mac_note is not None) is mac
    finally:
        win.close()


# ------------------------------------------------- (d) Datei transkribieren
@pytest.fixture
def fake_tf(monkeypatch):
    calls = {}

    def transcribe(path, cfg, timeout=600, *, progress=None, cancel_event=None,
                   output_format="text"):
        calls.update(path=path, fmt=output_format, cancel=cancel_event)
        progress(30.0, 60.0)
        if calls.get("cancel_now"):
            cancel_event.set()
            return None
        return {"text": "hallo welt", "srt": "1\n00:00:00,000 --> 00:00:01,000\nhallo\n",
                "vtt": "WEBVTT\n\n00:00.000 --> 00:01.000\nhallo\n"}[output_format]
    monkeypatch.setattr(center.transcribe_file, "transcribe_file", transcribe)
    monkeypatch.setattr(center.transcribe_file, "have_ffmpeg", lambda: True)
    return calls


@pytest.mark.parametrize("fmt,start", [("text", "hallo welt"), ("srt", "1\n00:00"),
                                       ("vtt", "WEBVTT")])
def test_file_format_progress_and_no_automatic_copy(c, fake_tf, monkeypatch, fmt, start):
    copied = []
    monkeypatch.setattr(center, "clip_copy", copied.append)
    progress = []
    c.bridge.file_progress.connect(lambda d, t: progress.append(c.file_bar.value()))
    c.file_format.setCurrentIndex(c.file_format.findData(fmt))
    c.start_file("/tmp/talk.wav", run_async=False)
    assert fake_tf["fmt"] == fmt and fake_tf["cancel"] is not None
    assert progress == [500]                       # 30 von 60 s
    assert c.file_result.toPlainText().startswith(start)
    assert copied == []                            # nichts ohne Nutzeraktion
    assert c.file_pick.isEnabled() and not c.file_cancel.isEnabled()
    c.file_copy.click()
    assert copied == [c.file_result.toPlainText()]


def test_cancel_reaches_transcribe_file(c, fake_tf):
    fake_tf["cancel_now"] = True
    c.start_file("/tmp/talk.wav", run_async=False)
    assert c.file_status.text() == i18n.tr("file_canceled")
    assert c.file_result.toPlainText() == ""


def test_cancel_button_sets_the_event(c):
    import threading
    ev = c._file_cancel_event = threading.Event()
    c.file_cancel.setEnabled(True)
    c.file_cancel.click()
    assert ev.is_set() and not c.file_cancel.isEnabled()


def test_file_formats_offered(c):
    assert [c.file_format.itemData(i) for i in range(c.file_format.count())] == \
        ["text", "srt", "vtt"]


# ------------------------------------------------------- (e) Per-App-Profile
def test_profiles_button_creates_once_and_opens(c, tmp_path, monkeypatch):
    from quassel import profiles
    path = tmp_path / "profiles.ini"
    monkeypatch.setattr(profiles, "PROFILES", str(path))
    opened = []
    monkeypatch.setattr(center.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toLocalFile()))
    c.profiles_btn.click()
    assert path.read_text(encoding="utf-8") == profiles.DEFAULTS
    assert opened == [str(path)]
    assert c.profiles_status.text().startswith("Created")
    path.write_text("[profile:Mine]\nmatch = x\n", encoding="utf-8")
    c.profiles_btn.click()                          # vorhandene Datei bleibt
    assert "Mine" in path.read_text(encoding="utf-8")
    assert c.profiles_status.text().startswith("Opened")
    page = c.stack.widget(c._page_keys.index("nav_dictating"))
    assert page.isAncestorOf(c.profiles_btn)


def test_profiles_failure_is_reported(c, monkeypatch):
    from quassel import profiles

    def boom(path=None):
        raise OSError("read-only")
    monkeypatch.setattr(profiles, "write_defaults", boom)
    opened = []
    monkeypatch.setattr(center.QDesktopServices, "openUrl", opened.append)
    c.profiles_btn.click()
    assert opened == [] and c.profiles_status.text().startswith("✕")


def test_no_page_needs_horizontal_scrolling(c, qapp):
    """Lange Einträge (Dropdowns, Pfade) dürfen keine Seite breiter als das
    Fenster machen. Offscreen-Plattform: kein sichtbares Fenster."""
    c.resize(760, 900)
    c.show()
    try:
        too_wide = []
        for i, key in enumerate(c._page_keys):
            c.sidebar.setCurrentRow(i)
            qapp.processEvents()
            if c.stack.widget(i).horizontalScrollBar().maximum() > 0:
                too_wide.append(key)
        assert too_wide == []
    finally:
        c.hide()
