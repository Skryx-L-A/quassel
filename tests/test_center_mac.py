"""macOS-Zweige des Kontrollzentrums: kein systemctl, Autostart via
LaunchAgent (Plist + launchctl), Server-Neustart über server_mac."""
import configparser
import os
import plistlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from quassel import center  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from quassel import beep, config  # noqa: E402


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(center, "IS_WINDOWS", False)
    monkeypatch.setattr(center, "IS_MAC", True)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))


@pytest.fixture
def fake_outputs(monkeypatch):
    """Steht für quassel.beep.list_outputs(). Kein Test fragt echte Geräte ab:
    das Kontrollzentrum soll hier gegen eine bekannte Liste geprüft werden, nicht
    gegen das, was gerade am Rechner steckt."""
    outputs = [("Bluetooth-Kopfhörer", "Bluetooth-Kopfhörer"),
               ("MacBook Pro-Lautsprecher", "MacBook Pro-Lautsprecher")]
    monkeypatch.setattr(beep, "list_outputs", lambda: outputs, raising=False)
    return outputs


@pytest.fixture
def agent_plist(tmp_path, monkeypatch):
    p = tmp_path / "de.skryx.quassel.plist"
    monkeypatch.setattr(center, "LAUNCH_AGENT_PLIST", str(p))
    return p


@pytest.fixture
def runs(monkeypatch):
    calls = []
    monkeypatch.setattr(center.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd))
    return calls


def test_sysctl_is_noop_on_mac(mac, runs):
    r = center.sysctl("is-enabled", "--quiet", "quasseld")
    assert r.returncode == 1
    assert runs == []           # nie systemctl aufrufen


def test_daemon_active_true_on_mac(mac, runs):
    assert center.daemon_active() is True
    assert runs == []


def test_restart_server_targets_linux_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(center, "IS_WINDOWS", False)
    monkeypatch.setattr(center, "IS_MAC", False)
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    monkeypatch.setattr(center, "sysctl", lambda *args: calls.append(args))
    center.restart_server()
    assert calls == [("try-restart", "quassel-server@next.service")]


def test_autostart_enabled_reflects_plist(mac, agent_plist):
    assert center.autostart_enabled() is False
    agent_plist.write_bytes(b"x")
    assert center.autostart_enabled() is True


def test_autostart_on_writes_plist_and_bootstraps(mac, agent_plist, runs,
                                                  monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    center.mac_autostart_set(True)
    with open(agent_plist, "rb") as f:
        plist = plistlib.load(f)
    assert plist["Label"] == "de.skryx.quassel"
    assert plist["ProgramArguments"] == ["/usr/bin/open", "-a", "Quassel"]
    assert plist["RunAtLoad"] is True
    # bootout vor bootstrap: macht doppeltes Aktivieren idempotent
    assert runs == [["launchctl", "bootout",
                     f"gui/{os.getuid()}/de.skryx.quassel"],
                    ["launchctl", "bootstrap", f"gui/{os.getuid()}",
                     str(agent_plist)]]


def test_autostart_on_frozen_uses_bundle_path(mac, agent_plist, runs,
                                              monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable",
                        "/Applications/Quassel.app/Contents/MacOS/Quassel")
    center.mac_autostart_set(True)
    with open(agent_plist, "rb") as f:
        plist = plistlib.load(f)
    assert plist["ProgramArguments"] == ["/usr/bin/open",
                                         "/Applications/Quassel.app"]


def test_autostart_off_boots_out_and_removes_plist(mac, agent_plist, runs):
    agent_plist.write_bytes(b"x")
    center.mac_autostart_set(False)
    assert not agent_plist.exists()
    assert runs == [["launchctl", "bootout",
                     f"gui/{os.getuid()}/de.skryx.quassel"]]


def test_autostart_off_without_plist_is_quiet(mac, agent_plist, runs):
    center.mac_autostart_set(False)     # kein Plist vorhanden: kein Fehler
    assert not agent_plist.exists()


def test_profile_autostart_cannot_touch_default_mac_agent(
        mac, agent_plist, runs, monkeypatch):
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    agent_plist.write_bytes(b"installed app")
    assert center.autostart_enabled() is False
    with pytest.raises(ValueError, match="profile"):
        center.mac_autostart_set(False)
    with pytest.raises(ValueError, match="profile"):
        center.mac_autostart_set(True)
    assert agent_plist.read_bytes() == b"installed app"
    assert runs == []


def test_profile_autostart_cannot_touch_default_windows_registry(monkeypatch):
    monkeypatch.setattr(center, "IS_WINDOWS", True)
    monkeypatch.setattr(center, "IS_MAC", False)
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    assert center.autostart_enabled() is False
    with pytest.raises(ValueError, match="profile"):
        center.autostart_set(False)


def test_mac_app_path_none_when_not_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert center.mac_app_path() is None


def test_restart_server_uses_server_mac(mac, monkeypatch):
    from quassel import server_mac
    calls = []
    monkeypatch.setattr(server_mac, "kill_orphans",
                        lambda: calls.append("kill"))
    monkeypatch.setattr(server_mac, "port_in_use", lambda **kw: False)
    monkeypatch.setattr(server_mac, "start", lambda: calls.append("start"))
    center.restart_server()
    assert calls == ["kill", "start"]


def test_history_and_file_copy_use_mac_clipboard(mac, monkeypatch, qapp, isolated_cfg):
    copied = []
    monkeypatch.setattr(center, "clip_copy", copied.append)
    c = center.Center()
    try:
        item = type("Item", (), {"data": lambda self, role: "again"})()
        c.on_hist_click(item)
        c.on_file_done("ok", "file result")
        assert copied == ["again"]            # Ergebnis erst auf Knopfdruck
        c.file_copy.click()
    finally:
        c.close()
    assert copied == ["again", "file result"]


def test_dictionary_shows_effective_words(mac, qapp, isolated_cfg):
    c = center.Center()
    try:
        c.dict_edit.setPlainText("old\nnewest")
        assert "newest" in c.dict_effective.text()
        assert "(2)" in c.dict_effective.text()
    finally:
        c.close()


# --------------------------------- Signaltoene: Ausgabegeraet (nur macOS)

def test_beep_out_combo_lists_system_first(mac, qapp, isolated_cfg, fake_outputs):
    c = center.Center()
    try:
        assert c.beep_out is not None
        assert c.beep_out.itemData(0) == "system"
        assert c.beep_out.count() == 1 + len(fake_outputs)
        assert c.beep_out.currentData() == "system"      # Vorgabe
    finally:
        c.close()


def test_beep_out_combo_preselects_saved_device(mac, qapp, isolated_cfg, fake_outputs):
    config.save({("behavior", "beep_output"): "Bluetooth-Kopfhörer"})
    c = center.Center()
    try:
        assert c.beep_out.currentData() == "Bluetooth-Kopfhörer"
    finally:
        c.close()


def test_beep_out_combo_keeps_stale_saved_device(mac, qapp, isolated_cfg, fake_outputs):
    """Ein Geraetename, der beim Oeffnen nicht mehr unter den verfuegbaren
    Ausgaben auftaucht (z.B. abgezogenes USB-Headset), bleibt trotzdem als
    Eintrag erhalten und ausgewaehlt — sonst wirft das blosse Ansehen des
    Fensters die Einstellung stillschweigend auf 'system' zurueck."""
    config.save({("behavior", "beep_output"): "USB-Mikro (abgezogen)"})
    c = center.Center()
    try:
        assert c.beep_out.findData("USB-Mikro (abgezogen)") >= 0
        assert c.beep_out.currentData() == "USB-Mikro (abgezogen)"
    finally:
        c.close()


def test_save_settings_writes_selected_beep_output(mac, qapp, isolated_cfg, fake_outputs):
    c = center.Center()
    try:
        c.beep_out.setCurrentIndex(c.beep_out.findData("MacBook Pro-Lautsprecher"))
        saved = configparser.ConfigParser()
        saved.read(config.CONFIG)
        assert saved.get("behavior", "beep_output") == "MacBook Pro-Lautsprecher"
    finally:
        c.close()


# ------------------------------------------- H115 Tastennamen auf dem Mac
def test_chord_choices_and_hint_use_mac_key_names(mac, qapp, isolated_cfg, monkeypatch):
    from quassel import i18n
    monkeypatch.setattr(i18n, "IS_MAC", True)
    config.save({("ui", "language"): "en"})
    c = center.Center()
    try:
        items = [c.chord.itemText(i) for i in range(c.chord.count())]
        assert items == ["Control + Command (⌃⌘)", "Option + Command (⌥⌘)",
                         "Control + Option (⌃⌥)"]
        assert "Hold Control + Command = speak" in c.hint.text()
        assert "Meta" not in c.hint.text() and "Windows" not in c.hint.text()
    finally:
        c.close()
        i18n.set_language(None)


# ------------------------------------------- H113 konkrete Fehlermeldungen
@pytest.fixture
def mictest(mac, qapp, isolated_cfg, tmp_path, monkeypatch):
    """Kontrollzentrum mit eigenem Datenordner und mitgeschnittenen Meldungen;
    nie ein echtes Mikrofon, nie ein echter Server."""
    from quassel import i18n
    monkeypatch.setattr(config, "DATADIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "SERVERENV", str(tmp_path / "server.env"))
    config.save({("ui", "language"): "en"})
    c = center.Center()
    c.msgs = []
    c.bridge.message.connect(c.msgs.append)
    yield c
    c.close()
    i18n.set_language(None)


class _DeadRecorder:
    def __init__(self, raw_path=None):
        pass

    def start(self, mic):
        return False


def test_mic_test_names_missing_mac_permission(mictest, monkeypatch):
    from quassel import audio
    monkeypatch.setattr(audio, "Recorder", _DeadRecorder)
    monkeypatch.setattr(center, "mac_backend", lambda: "sd16")
    monkeypatch.setattr(center, "mac_mic_permission", lambda: "denied")
    mictest._mictest_run()
    assert mictest.msgs == ["✕ " + center.tr("err_mic_denied")]
    assert "Privacy & Security → Microphone" in mictest.msgs[0]


def test_mic_test_busy_microphone_is_not_called_a_permission_problem(mictest, monkeypatch):
    from quassel import audio
    monkeypatch.setattr(audio, "Recorder", _DeadRecorder)
    monkeypatch.setattr(center, "mac_backend", lambda: "sd16")
    monkeypatch.setattr(center, "mac_mic_permission", lambda: "granted")
    mictest._mictest_run()
    assert mictest.msgs == ["✕ " + center.tr("err_mic_open")]


def test_mic_test_silence_says_nothing_captured(mictest, monkeypatch):
    monkeypatch.setattr(mictest, "_mictest_record", lambda: b"\0" * 100)
    mictest._mictest_run()
    assert mictest.msgs == ["✕ " + center.tr("mic_nothing")]


def test_mic_test_names_a_missing_model(mictest, tmp_path, monkeypatch):
    with open(config.SERVERENV, "w", encoding="utf-8") as f:
        f.write("MODEL_PATH=%s\n" % (tmp_path / "ggml-small.bin"))
    monkeypatch.setattr(mictest, "_mictest_record", lambda: b"\1\0" * 8000)
    monkeypatch.setattr(center.whisperclient, "ensure_server", lambda: False)
    mictest._mictest_run()
    assert mictest.msgs[-1] == "✕ " + center.tr("err_model_missing", model="ggml-small.bin")
    assert not os.path.exists(os.path.join(config.DATADIR, "mictest.wav"))


def test_mic_test_server_down_with_model_present(mictest, tmp_path, monkeypatch):
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"x")
    with open(config.SERVERENV, "w", encoding="utf-8") as f:
        f.write("MODEL_PATH=%s\n" % model)
    monkeypatch.setattr(mictest, "_mictest_record", lambda: b"\1\0" * 8000)
    monkeypatch.setattr(center.whisperclient, "ensure_server", lambda: False)
    mictest._mictest_run()
    assert mictest.msgs[-1] == "✕ " + center.tr("err_server_down")


def test_mic_test_transcription_failure_is_not_called_unreachable(mictest, monkeypatch):
    monkeypatch.setattr(mictest, "_mictest_record", lambda: b"\1\0" * 8000)
    monkeypatch.setattr(center.whisperclient, "ensure_server", lambda: True)
    monkeypatch.setattr(center.whisperclient, "transcribe", lambda *a, **k: None)
    mictest._mictest_run()
    assert mictest.msgs[-1] == "✕ " + center.tr("err_transcribe")
    assert not os.path.exists(os.path.join(config.DATADIR, "mictest.wav"))


def test_mac_mic_permission_is_a_known_answer():
    """Echte, rein lesende Statusabfrage (kein Dialog, kein Mikrofon)."""
    assert center.mac_mic_permission() in ("granted", "denied", "undetermined", None)
