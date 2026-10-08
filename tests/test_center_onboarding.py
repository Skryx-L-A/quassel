"""Erst-Einrichtung (G110) offscreen: Schritte, Rechte-Anzeige, Mikrofontest
ohne Wiedergabe, Modellstatus, Übungsfeld. Keine echte Rechteabfrage, kein
Mikrofon, kein Dialog wird ausgeführt (exec) oder sichtbar."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication              # noqa: E402

from quassel import center, config, i18n               # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "SERVERENV", str(tmp_path / "server.env"))
    i18n.set_language("en")
    yield
    i18n.set_language(None)


MAC_ROWS = [("ob_perm_mic", "ok", "x-apple:mic"),
            ("ob_perm_input", "missing", "x-apple:listen"),
            ("ob_perm_access", "unknown", "x-apple:access")]


def make(record=None, rows=None, models=("ok", "ggml-small.bin")):
    return center.OnboardingDialog(None, "Control + Command", record_fn=record,
                                   permissions=lambda: list(rows or []),
                                   models=lambda: models)


def tone(n=40000, amp=3000):
    return struct.pack("<%dh" % n, *([amp, -amp] * (n // 2)))


def test_five_steps_with_back_and_finish():
    d = make()
    try:
        assert d.pages.count() == 5
        assert d.step_lbl.text() == "Step 1 of 5"
        assert not d.back_btn.isEnabled()
        for _ in range(4):
            d.next_btn.click()
        assert d.pages.currentIndex() == 4
        assert d.next_btn.text() == "Finish"
        assert d.title.text() == "Try it now"
        d.back_btn.click()
        assert d.pages.currentIndex() == 3
    finally:
        d.close()


def test_permission_rows_show_status_and_settings_buttons():
    d = make(rows=MAC_ROWS)
    try:
        ok_status, ok_label, ok_btn = d.perm_rows["ob_perm_mic"]
        assert ok_label.text() == "✓ Allowed" and ok_btn is None
        _s, miss_label, miss_btn = d.perm_rows["ob_perm_input"]
        assert miss_label.text() == "Not allowed yet"
        assert miss_btn is not None and "Input Monitoring" in miss_btn.accessibleName()
        _s, unk_label, unk_btn = d.perm_rows["ob_perm_access"]
        assert unk_label.text() == "Asked on first use" and unk_btn is not None
    finally:
        d.close()


def test_windows_needs_no_permissions():
    d = make(rows=[])
    try:
        assert d.perm_rows == {}
    finally:
        d.close()


def test_mic_test_reports_sound_without_playback():
    d = make(record=tone)
    try:
        d.start_mic_test(run_async=False)
        assert d.mic_result.text() == "✓ " + i18n.tr("ob_mic_ok")
        assert d.mic_bar.value() > 50
        assert d.mic_btn.isEnabled()
    finally:
        d.close()


def test_mic_test_flags_a_silent_microphone():
    d = make(record=lambda: b"\0\0" * 40000)
    try:
        d.start_mic_test(run_async=False)
        assert d.mic_result.text() == "✕ " + i18n.tr("mic_silent")
        assert d.mic_bar.value() == 0
    finally:
        d.close()


def test_mic_test_names_the_error():
    def denied():
        raise center.MicTestError(i18n.tr("err_mic_denied"))
    d = make(record=denied)
    try:
        d.start_mic_test(run_async=False)
        assert d.mic_result.text() == "✕ " + i18n.tr("err_mic_denied")
    finally:
        d.close()


def test_mic_test_disabled_without_a_recorder():
    d = make(record=None)
    try:
        assert not d.mic_btn.isEnabled()
    finally:
        d.close()


@pytest.mark.parametrize("models,text,button", [
    (("ok", "ggml-small.bin"), "✓ Ready: ggml-small.bin", False),
    (("missing", "ggml-large.bin"), None, True),
    (("none", ""), None, True),
])
def test_model_step(models, text, button):
    d = make(models=models)
    try:
        if text:
            assert d.model_lbl.text() == text
        assert (d.model_btn is not None) is button
        if button:
            d.model_btn.click()
            assert d.open_page == "speech" and d.result() == center.QDialog.Accepted
    finally:
        d.close()


def test_wake_button_keeps_the_old_meaning():
    d = make()
    try:
        d._go_settings()
        assert d.open_settings is True and d.open_page == "wake"
    finally:
        d.close()


def test_try_step_has_a_named_practice_field():
    d = make()
    try:
        d.go(4)
        assert d.try_field.accessibleName() == "Practice field"
    finally:
        d.close()


def test_model_status_reads_server_env(tmp_path):
    assert center.model_status() == ("none", "")
    model = tmp_path / "ggml-small.bin"
    with open(config.SERVERENV, "w", encoding="utf-8") as f:
        f.write("MODEL_PATH=%s\n" % model)
    assert center.model_status() == ("missing", "ggml-small.bin")
    model.write_bytes(b"x")
    assert center.model_status() == ("ok", "ggml-small.bin")


def test_clip_level_scale():
    assert center.clip_level(b"") == 0.0
    assert center.clip_level(b"\0\0" * 100) == 0.0
    assert center.clip_level(tone(amp=4000)) == pytest.approx(0.5)


def test_permission_status_is_read_only_and_well_formed():
    """Echte Statusabfrage dieser Plattform; nur lesen, keine Rechte-Dialoge."""
    for key, status, url in center.permission_status():
        assert key in i18n.STRINGS and status in ("ok", "missing", "unknown")


def test_center_opens_the_requested_page(monkeypatch):
    c = center.Center()
    try:
        c.show_page("nav_ai")
        assert c.sidebar.currentRow() == c._page_keys.index("nav_ai")
        c.show_page("does-not-exist")                 # bleibt, wo es ist
        assert c.sidebar.currentRow() == c._page_keys.index("nav_ai")
    finally:
        c.close()


def test_offscreen_render_of_the_permissions_step(tmp_path):
    d = make(rows=MAC_ROWS)
    try:
        d.go(1)
        img = d.grab().toImage()
        assert img.width() >= 520 and not img.isNull()
    finally:
        d.close()


# ------------------------------------------- G111 kürzere Seiten, Beta gesammelt
def _page(c, key):
    return c.stack.widget(c._page_keys.index(key))


def test_pages_are_short():
    """Höchstens drei Abschnitte je Seite; vorher hatten Allgemein vier und
    Spracherkennung fünf."""
    from PySide6.QtWidgets import QGroupBox
    c = center.Center()
    try:
        counts = {key: len(_page(c, key).findChildren(QGroupBox)) for key in c._page_keys}
        assert max(counts.values()) <= 3, counts
    finally:
        c.close()


def test_beta_features_are_grouped_on_their_own_page():
    config.save({("ui", "language"): "en"})
    c = center.Center()
    try:
        assert c._page_keys[-3:] == ["nav_beta", "nav_system", "about"]
        beta = _page(c, "nav_beta")
        assert beta.isAncestorOf(c.wake_on) and beta.isAncestorOf(c.wake_phrase)
        assert not _page(c, "nav_speech").isAncestorOf(c.wake_on)
        assert c.sidebar.item(c._page_keys.index("nav_beta")).text() == "Beta features"
    finally:
        c.close()


def test_settings_keep_their_place_after_the_split():
    c = center.Center()
    try:
        assert _page(c, "nav_pill").isAncestorOf(c.pill_size)
        assert _page(c, "nav_dictating").isAncestorOf(c.stream_on)
        assert _page(c, "nav_dictating").isAncestorOf(c.beep_chk)
        assert _page(c, "nav_mic").isAncestorOf(c.mic)
        assert _page(c, "nav_speech").isAncestorOf(c.model)
        assert _page(c, "nav_general").isAncestorOf(c.chord)
    finally:
        c.close()


def test_onboarding_wake_button_opens_the_beta_page(monkeypatch):
    class FakeDialog:
        def __init__(self, *a, **k):
            self.open_page = "wake"
            self.open_settings = True

        def exec(self):
            return 1
    monkeypatch.setattr(center, "OnboardingDialog", FakeDialog)
    c = center.Center()
    try:
        c.cfg.onboarded = False
        c._maybe_onboard()
        assert c.sidebar.currentRow() == c._page_keys.index("nav_beta")
    finally:
        c.close()
