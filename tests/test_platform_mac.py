"""Tests für quassel/platform_mac.py: Logik gemockt (subprocess/Clipboard/
CoreAudio), damit die Suite auch ohne mac-Frameworks headless läuft.
Reine Integrationsschritte (echtes Quartz/AppKit) sind mit skipif auf
sys.platform != 'darwin' bzw. fehlendes pyobjc markiert."""
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import quassel.platform_mac as pm

try:
    import Quartz  # noqa: F401
    import AppKit  # noqa: F401
    _HAS_MAC_FRAMEWORKS = True
except ImportError:
    _HAS_MAC_FRAMEWORKS = False


# ---------------------------------------------------------- Clipboard / Paste
# ECHTE Threads/Timer mit test-skalierten Delays — genau die Verzahnungen aus
# dem Review (Doppel-Paste im KI-Pfad, Nutzer-Copy im Restore-Fenster, leeres
# Original) laufen hier real ab.

def _wait_restores(mult=4.0):
    """Warten, bis alle (test-skalierten) Restore-Timer gefeuert haben."""
    time.sleep(pm.RESTORE_DELAY * mult)


@pytest.fixture(autouse=True)
def _clip_slot_reset():
    """Restore-Slot vor/nach jedem Test leeren; hängende Timer canceln."""
    def reset():
        with pm._clip_lock:
            if pm._restore_timer is not None:
                pm._restore_timer.cancel()
            pm._restore_timer = None
            pm._saved_original = None
            pm._last_written = None
    reset()
    yield
    reset()


@pytest.fixture
def clip(monkeypatch):
    """Fake-Pasteboard + kurze Delays. Scheduling/Threads bleiben ECHT."""
    board = {"text": "user-original", "concealed": False}

    def copy(text, concealed=False):
        board["text"] = text
        board["concealed"] = concealed

    monkeypatch.setattr(pm, "clip_read", lambda: board["text"])
    monkeypatch.setattr(pm, "clip_copy", copy)
    monkeypatch.setattr(pm, "clip_clear",
                        lambda: (board.update(text="", concealed=False)))
    monkeypatch.setattr(pm, "_send_cmd_v", lambda: None)
    monkeypatch.setattr(pm, "PASTE_SETTLE", 0.0)
    monkeypatch.setattr(pm, "RESTORE_DELAY", 0.08)
    monkeypatch.setattr(pm, "STREAM_RESTORE_DELAY", 0.05)
    return board


def test_paste_writes_concealed_then_restores_original(clip):
    pm.paste("diktat")
    assert clip["text"] == "diktat"
    assert clip["concealed"] is True      # Clipboard-Manager überspringen uns
    _wait_restores()
    assert clip["text"] == "user-original"
    assert clip["concealed"] is False     # Original wird normal geschrieben


def test_ai_double_paste_keeps_user_original(clip):
    """Review-H1-Kernszenario: paste(mech) -> paste(final) liest den eigenen
    Paste — am Ende muss das NUTZER-Original zurückkommen, nie 'mech'."""
    pm.paste("mech text")
    time.sleep(pm.RESTORE_DELAY / 3)      # innerhalb des Restore-Fensters
    pm.paste("final text")                # clip_read() == "mech text" hier
    assert clip["text"] == "final text"
    _wait_restores()
    assert clip["text"] == "user-original"


def test_rapid_second_dictation_keeps_user_original(clip):
    pm.paste("erstes diktat")
    time.sleep(pm.RESTORE_DELAY / 3)
    pm.paste("zweites diktat")
    _wait_restores()
    assert clip["text"] == "user-original"


def test_empty_original_clears_pasteboard(clip):
    clip["text"] = ""                      # Nutzer hatte nichts im Clipboard
    pm.paste("geheimes diktat")
    _wait_restores()
    assert clip["text"] == ""              # Diktat bleibt NICHT liegen


def test_user_copy_during_window_is_not_overwritten(clip):
    pm.paste("diktat")
    clip["text"] = "frisch vom nutzer kopiert"   # Copy im Restore-Fenster
    _wait_restores()
    assert clip["text"] == "frisch vom nutzer kopiert"


def test_type_chunk_empty_is_noop(clip):
    before = dict(clip)
    pm.type_chunk("")
    assert clip == before


def test_type_chunk_writes_concealed(clip):
    pm.type_chunk("streaming text")
    assert clip["text"] == "streaming text"
    assert clip["concealed"] is True


def test_streaming_roundtrip_restores_original(clip):
    tok = pm.streaming_begin()
    assert tok == "user-original"
    pm.type_chunk("häppchen eins")
    pm.type_chunk("häppchen zwei")
    pm.streaming_restore(tok)
    _wait_restores()
    assert clip["text"] == "user-original"


def test_type_chunk_cancels_pending_paste_restore(clip):
    pm.paste("altes diktat")
    pm.type_chunk("chunk")                 # bricht den anstehenden Restore ab
    _wait_restores()
    assert clip["text"] == "chunk"         # kein Timer mehr aktiv
    pm.streaming_restore("")               # Slot trägt Original weiter
    _wait_restores()
    assert clip["text"] == "user-original"


def test_streaming_restore_with_empty_slot_and_token_clears(clip):
    clip["text"] = "diktat-rest"
    with pm._clip_lock:
        pm._last_written = "diktat-rest"   # wir haben zuletzt geschrieben
    pm.streaming_restore("")
    _wait_restores()
    assert clip["text"] == ""              # leeres Original -> leeren


# ---------------------------------------------------------------- Backspaces

def test_send_backspaces_caps_at_4000(monkeypatch):
    count = [0]
    monkeypatch.setattr(pm, "_key_event", lambda code, down: count.__setitem__(0, count[0] + 1))
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)

    pm.send_backspaces(10000)

    # 4000 Backspaces * 2 Events (down+up) = 8000
    assert count[0] == 8000


def test_send_backspaces_sends_down_and_up_per_press(monkeypatch):
    events = []
    monkeypatch.setattr(pm, "_key_event", lambda code, down: events.append((code, down)))
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)

    pm.send_backspaces(3)

    assert events == [
        (pm.KEY_BACKSPACE, True), (pm.KEY_BACKSPACE, False),
        (pm.KEY_BACKSPACE, True), (pm.KEY_BACKSPACE, False),
        (pm.KEY_BACKSPACE, True), (pm.KEY_BACKSPACE, False),
    ]


def test_send_backspaces_batches_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(pm, "_key_event", lambda code, down: None)
    monkeypatch.setattr(pm.time, "sleep", lambda s: sleeps.append(s))

    pm.send_backspaces(85)   # 2 volle 40er-Batches + Rest

    assert len(sleeps) == 2


def test_send_enter_sends_down_and_up(monkeypatch):
    events = []
    monkeypatch.setattr(pm, "_key_event", lambda code, down: events.append((code, down)))

    pm.send_enter()

    assert events == [(pm.KEY_ENTER, True), (pm.KEY_ENTER, False)]


def _fake_quartz(monkeypatch, flags, posts):
    """CGEvent-Primitiven faken — _key_event selbst bleibt UNGEPATCHT,
    damit der Test das echte Flag-Nullen sieht (Review H5)."""
    monkeypatch.setattr(pm, "_HAS_OBJC", True)
    monkeypatch.setattr(pm, "CGEventCreateKeyboardEvent",
                        lambda src, code, down: ("ev", code, down),
                        raising=False)
    monkeypatch.setattr(pm, "CGEventSetFlags",
                        lambda ev, f: flags.append((ev, f)), raising=False)
    monkeypatch.setattr(pm, "CGEventPost",
                        lambda tap, ev: posts.append(ev), raising=False)
    monkeypatch.setattr(pm, "kCGHIDEventTap", 0, raising=False)


def test_key_event_zeroes_modifier_flags(monkeypatch):
    """Synthetische Enter/Backspace dürfen physisch gehaltene Modifier NICHT
    erben (Option+Backspace löscht Wörter, Cmd+Return verschickt Mails)."""
    flags, posts = [], []
    _fake_quartz(monkeypatch, flags, posts)

    pm.send_enter()

    assert len(posts) == 2                       # down + up gepostet
    assert len(flags) == 2                       # für JEDES Event Flags gesetzt
    assert all(f == 0 for _ev, f in flags)       # und zwar explizit auf 0


def test_backspaces_zero_flags_on_every_event(monkeypatch):
    flags, posts = [], []
    _fake_quartz(monkeypatch, flags, posts)
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)

    pm.send_backspaces(3)

    assert len(posts) == 6
    assert len(flags) == 6
    assert all(f == 0 for _ev, f in flags)


# ------------------------------------------------------------- Bluetooth-Mic

def test_mic_is_bluetooth_by_name_positive():
    assert pm.mic_is_bluetooth("AirPods Pro") is True
    assert pm.mic_is_bluetooth("Bluetooth Headset Mic") is True


def test_mic_is_bluetooth_by_name_negative():
    assert pm.mic_is_bluetooth("MacBook Pro Microphone") is False


def test_mic_is_bluetooth_default_queries_coreaudio(monkeypatch):
    monkeypatch.setattr(pm, "_default_input_is_bluetooth", lambda: True)
    assert pm.mic_is_bluetooth("default") is True
    assert pm.mic_is_bluetooth() is True


def test_mic_is_bluetooth_swallows_exceptions(monkeypatch):
    def boom():
        raise RuntimeError("CoreAudio kaputt")
    monkeypatch.setattr(pm, "_default_input_is_bluetooth", boom)
    assert pm.mic_is_bluetooth() is False


def test_default_input_is_bluetooth_true_for_bt_transport(monkeypatch):
    monkeypatch.setattr(pm, "_coreaudio_lib", lambda: object())
    values = iter([42, pm._kAudioDeviceTransportTypeBluetooth])
    monkeypatch.setattr(pm, "_get_property_uint32", lambda lib, obj, sel: next(values))
    assert pm._default_input_is_bluetooth() is True


def test_default_input_is_bluetooth_true_for_ble_transport(monkeypatch):
    monkeypatch.setattr(pm, "_coreaudio_lib", lambda: object())
    values = iter([42, pm._kAudioDeviceTransportTypeBluetoothLE])
    monkeypatch.setattr(pm, "_get_property_uint32", lambda lib, obj, sel: next(values))
    assert pm._default_input_is_bluetooth() is True


def test_default_input_is_bluetooth_false_for_builtin(monkeypatch):
    monkeypatch.setattr(pm, "_coreaudio_lib", lambda: object())
    builtin_transport = pm._fourcc("bltn")
    values = iter([42, builtin_transport])
    monkeypatch.setattr(pm, "_get_property_uint32", lambda lib, obj, sel: next(values))
    assert pm._default_input_is_bluetooth() is False


def test_default_input_is_bluetooth_false_when_no_lib(monkeypatch):
    monkeypatch.setattr(pm, "_coreaudio_lib", lambda: None)
    assert pm._default_input_is_bluetooth() is False


def test_default_input_is_bluetooth_false_when_no_device(monkeypatch):
    monkeypatch.setattr(pm, "_coreaudio_lib", lambda: object())
    monkeypatch.setattr(pm, "_get_property_uint32", lambda lib, obj, sel: None)
    assert pm._default_input_is_bluetooth() is False


# --------------------------------------------------------------------- Notify

def test_notify_calls_osascript(monkeypatch):
    calls = []
    monkeypatch.setattr(pm.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))

    pm.notify("Diktat fertig", ms=2000)

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][0] == "osascript"
    assert "Diktat fertig" in args[0][2]
    assert kwargs.get("check") is False


def test_notify_escapes_quotes():
    quoted = pm._osa_quote('sagt "hallo"')
    assert quoted == '"sagt \\"hallo\\""'


def test_osa_quote_escapes_newlines():
    # rohe Newlines wären im AppleScript-Literal ein Syntaxfehler ->
    # Notification ginge stumm verloren
    assert pm._osa_quote("zeile1\nzeile2") == '"zeile1\\nzeile2"'
    assert pm._osa_quote("a\r\nb\rc") == '"a\\nb\\nc"'


def test_osa_quote_strips_control_chars():
    assert pm._osa_quote("ding\x07dong\x00") == '"dingdong"'
    assert pm._osa_quote("tab\tbleibt") == '"tab\tbleibt"'


# ------------------------------------------------------------------- Ducking

def test_duck_apply_all_mutes_and_remembers_previous_state(monkeypatch):
    calls = []
    monkeypatch.setattr(pm, "_output_muted", lambda: False)
    monkeypatch.setattr(pm, "_osa", lambda script: calls.append(script))

    token = pm.duck_apply("all")

    assert token == {"was_muted": False}
    assert any("with output muted" in c for c in calls)


def test_duck_restore_all_unmutes_when_was_not_muted(monkeypatch):
    calls = []
    monkeypatch.setattr(pm, "_osa", lambda script: calls.append(script))

    pm.duck_restore("all", {"was_muted": False})

    assert any("without output muted" in c for c in calls)


def test_duck_restore_all_leaves_muted_when_was_already_muted(monkeypatch):
    calls = []
    monkeypatch.setattr(pm, "_osa", lambda script: calls.append(script))

    pm.duck_restore("all", {"was_muted": True})

    assert calls == []


def test_duck_apply_music_pauses_playing_apps(monkeypatch):
    paused = []
    monkeypatch.setattr(pm, "_playing_apps", lambda: ["Music", "Spotify"])
    monkeypatch.setattr(pm, "_pause_app", lambda app: paused.append(app))

    token = pm.duck_apply("music")

    assert token == {"apps": ["Music", "Spotify"]}
    assert paused == ["Music", "Spotify"]


def test_duck_restore_music_resumes_only_paused_apps(monkeypatch):
    resumed = []
    monkeypatch.setattr(pm, "_play_app", lambda app: resumed.append(app))

    pm.duck_restore("music", {"apps": ["Spotify"]})

    assert resumed == ["Spotify"]


def test_duck_apply_unknown_mode_returns_none():
    assert pm.duck_apply("unknown") is None


def test_duck_restore_without_token_is_safe(monkeypatch):
    calls = []
    monkeypatch.setattr(pm, "_osa", lambda script: calls.append(script))
    pm.duck_restore("all", None)
    pm.duck_restore("music", {})
    assert calls == []


# ------------------------------------------------------ mediacontrol-Wiring

def test_mediacontrol_selects_mac_backend_on_darwin(monkeypatch):
    import importlib
    monkeypatch.setattr(sys, "platform", "darwin")
    sys.modules.pop("quassel.mediacontrol", None)
    try:
        import quassel.mediacontrol as mc
        assert mc._backend is pm
    finally:
        # frisch re-importieren, damit spätere Tests das Modul im echten
        # Plattform-Zustand sehen (kein globaler Interpreter-Restzustand)
        sys.modules.pop("quassel.mediacontrol", None)
        monkeypatch.undo()
        importlib.import_module("quassel.mediacontrol")


# ------------------------------------------------- osascript mit harter Frist
# Jeder osascript-Aufruf läuft auf dem Thread, der auch den Hotkey bedient —
# ohne Frist hängt dort das ganze Diktat, wenn das Notification Center oder
# ein Player klemmt.

def _osa_raises_timeout(monkeypatch, seen):
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["timeout"] = kw.get("timeout")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(pm.subprocess, "run", fake_run)


def test_notify_passes_a_timeout_and_swallows_it(monkeypatch):
    seen = {}
    _osa_raises_timeout(monkeypatch, seen)
    pm.notify("hallo")                     # darf nicht durchschlagen
    assert seen["cmd"][0] == "osascript"
    assert seen["timeout"] == pm.OSA_TIMEOUT


def test_osa_returns_empty_stdout_on_timeout(monkeypatch):
    """Die Aufrufer lesen .stdout — bei Timeout muss ein Objekt zurückkommen,
    kein Ausnahmefehler: 'nicht stumm' bzw. 'es spielt nichts'."""
    seen = {}
    _osa_raises_timeout(monkeypatch, seen)
    assert pm._osa("beliebig").stdout == ""
    assert seen["timeout"] == pm.OSA_TIMEOUT
    assert pm._output_muted() is False
    assert pm._playing_apps() == []


def test_osa_passes_the_timeout_on_the_normal_path(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return SimpleNamespace(stdout="true", stderr="", returncode=0)
    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    assert pm._output_muted() is True
    assert seen["timeout"] == pm.OSA_TIMEOUT


def test_ducking_stays_harmless_when_osascript_hangs(monkeypatch):
    """duck_apply/duck_restore dürfen im Timeout-Fall nichts kaputt machen."""
    seen = {}
    _osa_raises_timeout(monkeypatch, seen)
    token = pm.duck_apply("all")
    assert token == {"was_muted": False}
    pm.duck_restore("all", token)          # keine Ausnahme
    assert pm.duck_apply("music") == {"apps": []}


# ------------------------------------------------------- Echte mac-Integration
# Nur ausführen, wenn tatsächlich auf macOS mit pyobjc-Frameworks getestet wird.

@pytest.mark.skipif(not (_HAS_MAC_FRAMEWORKS and sys.platform == "darwin"),
                    reason="benötigt echtes macOS mit pyobjc-Frameworks")
def test_clip_roundtrip_real_pasteboard():
    old = pm.clip_read()
    try:
        pm.clip_copy("quassel-platform-mac-test")
        assert pm.clip_read() == "quassel-platform-mac-test"
    finally:
        if old:
            pm.clip_copy(old)


@pytest.mark.skipif(not (_HAS_MAC_FRAMEWORKS and sys.platform == "darwin"),
                    reason="benötigt echtes macOS mit pyobjc-Frameworks")
def test_default_input_is_bluetooth_real_coreaudio_does_not_raise():
    # nur prüfen, dass der echte CoreAudio-Aufruf nicht crasht — der
    # tatsächliche Wert hängt vom angeschlossenen Mikrofon ab.
    result = pm._default_input_is_bluetooth()
    assert isinstance(result, bool)
