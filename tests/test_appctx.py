"""Audit G102: die aktive Anwendung erkennen — ohne echte Fenster, ohne
Hilfsprogramme. Jeder Plattformweg läuft hier gegen Attrappen; Fenstertitel
dürfen nirgends ins Ergebnis gelangen."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import appctx

SECRET_TITLE = "Kündigung Müller - Entwurf"


def fake_run(monkeypatch, answers, seen=None):
    """_run-Attrappe: argv[0..1] -> Ausgabe; alles mitschreiben."""
    def run(argv):
        if seen is not None:
            seen.append(list(argv))
        for key, out in answers.items():
            if tuple(argv[:len(key)]) == key:
                return out
        return None
    monkeypatch.setattr(appctx, "_run", run)


# ------------------------------------------------------------------ AppInfo
def test_make_normalizes_and_strips_paths():
    app = appctx.make("Com.Apple.Terminal", r"C:\Program Files\Code\Code.exe", "Code")
    assert app == appctx.AppInfo("com.apple.terminal", "code.exe", "code")
    assert app.identifiers() == ("com.apple.terminal", "code.exe", "code")
    assert appctx.make(process="/usr/bin/kitty").process == "kitty"


def test_identifiers_skip_empty_and_duplicates():
    assert appctx.make(process="kitty", wm_class="kitty").identifiers() == ("kitty",)
    assert appctx.UNKNOWN.identifiers() == ()
    assert not appctx.UNKNOWN.known
    assert appctx.UNKNOWN.label() == "unknown"


# --------------------------------------------------------------------- X11
def test_x11_reads_class_and_pid_but_never_the_title(monkeypatch):
    seen = []
    fake_run(monkeypatch, {
        ("xprop", "-root"): "_NET_ACTIVE_WINDOW: window id # 0x3e00007\n",
        ("xprop", "-id"): 'WM_CLASS = "gnome-terminal-server", "Gnome-terminal"\n'
                          "_NET_WM_PID = 4242\n",
    }, seen)
    monkeypatch.setattr(appctx, "_proc_name", lambda pid: "gnome-terminal-" if pid == 4242 else "")
    app = appctx._linux({"DISPLAY": ":0"})
    assert app == appctx.AppInfo("", "gnome-terminal-", "gnome-terminal")
    flat = " ".join(" ".join(a) for a in seen)
    assert "WM_NAME" not in flat and "_NET_WM_NAME" not in flat


def test_x11_without_active_window_is_unknown(monkeypatch):
    fake_run(monkeypatch, {("xprop", "-root"): "_NET_ACTIVE_WINDOW: window id # 0x0\n"})
    assert appctx._linux({"DISPLAY": ":0"}) is appctx.UNKNOWN
    fake_run(monkeypatch, {("xprop", "-root"): "_NET_ACTIVE_WINDOW:  not found.\n"})
    assert appctx._linux({"DISPLAY": ":0"}) is appctx.UNKNOWN
    fake_run(monkeypatch, {})                   # xprop fehlt / hängt
    assert appctx._linux({"DISPLAY": ":0"}) is appctx.UNKNOWN


def test_xprop_parsers_survive_garbage():
    assert appctx._xprop_window_id("x # 0xZZ") == ""
    assert appctx._xprop_window_id(None) == ""
    assert appctx._xprop_class_pid("WM_CLASS = \n_NET_WM_PID = abc") == ("", 0)


# ----------------------------------------------------------------- Wayland
def test_hyprland_uses_class_and_ignores_title(monkeypatch):
    fake_run(monkeypatch, {("hyprctl", "activewindow"): json.dumps(
        {"class": "kitty", "title": SECRET_TITLE, "pid": 77})})
    monkeypatch.setattr(appctx, "_proc_name", lambda pid: "kitty")
    app = appctx._linux({"WAYLAND_DISPLAY": "wayland-1",
                         "HYPRLAND_INSTANCE_SIGNATURE": "abc"})
    assert app == appctx.AppInfo("", "kitty", "kitty")
    assert SECRET_TITLE.lower() not in repr(app)


def test_sway_finds_the_focused_window(monkeypatch):
    tree = {"type": "root", "nodes": [{"type": "output", "nodes": [
        {"type": "workspace", "focused": False, "nodes": [
            {"type": "con", "focused": False, "app_id": "firefox", "pid": 1,
             "name": "other"},
            {"type": "con", "focused": True, "app_id": "foot", "pid": 2,
             "name": SECRET_TITLE},
        ], "floating_nodes": []}]}]}
    fake_run(monkeypatch, {("swaymsg", "-t"): json.dumps(tree)})
    monkeypatch.setattr(appctx, "_proc_name", lambda pid: {2: "foot"}.get(pid, ""))
    app = appctx._linux({"WAYLAND_DISPLAY": "wayland-1", "SWAYSOCK": "/run/sway"})
    assert app == appctx.AppInfo("", "foot", "foot")
    assert SECRET_TITLE.lower() not in repr(app)


def test_sway_xwayland_window_uses_its_class(monkeypatch):
    tree = {"type": "con", "focused": True, "app_id": None, "pid": 3,
            "window_properties": {"class": "Code", "title": SECRET_TITLE}}
    fake_run(monkeypatch, {("swaymsg", "-t"): json.dumps(tree)})
    monkeypatch.setattr(appctx, "_proc_name", lambda pid: "code")
    app = appctx._linux({"WAYLAND_DISPLAY": "w", "SWAYSOCK": "s"})
    assert app.wm_class == "code" and SECRET_TITLE.lower() not in repr(app)


def test_gnome_and_kde_wayland_are_honestly_unknown(monkeypatch):
    """xprop sähe nur XWayland-Fenster; dann lieber unbekannt."""
    seen = []
    fake_run(monkeypatch, {("xprop", "-root"): "_NET_ACTIVE_WINDOW: window id # 0x1\n"}, seen)
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0",
           "XDG_CURRENT_DESKTOP": "GNOME"}
    assert appctx._linux(env) is appctx.UNKNOWN
    assert seen == []


def test_broken_json_is_unknown(monkeypatch):
    fake_run(monkeypatch, {("hyprctl", "activewindow"): "not json"})
    assert appctx._hyprland() is appctx.UNKNOWN
    fake_run(monkeypatch, {("swaymsg", "-t"): "[]"})
    assert appctx._sway() is appctx.UNKNOWN


def test_run_has_a_deadline(monkeypatch):
    calls = {}

    def fake(argv, **kw):
        calls.update(kw)
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(appctx.subprocess, "run", fake)
    assert appctx._run(["xprop", "-root"]) is None
    assert 0 < calls["timeout"] <= 0.5


def test_proc_name_rejects_bad_pids():
    assert appctx._proc_name(None) == ""
    assert appctx._proc_name(0) == ""
    assert appctx._proc_name("x") == ""


# ------------------------------------------------------------------- macOS
class FakeURL:
    def __init__(self, name):
        self.name = name

    def lastPathComponent(self):
        return self.name


class FakeApp:
    def bundleIdentifier(self):
        return "com.apple.Terminal"

    def executableURL(self):
        return FakeURL("Terminal")

    def localizedName(self):
        return "Terminal"


def test_mac_uses_nsworkspace(monkeypatch):
    ws = SimpleNamespace(sharedWorkspace=lambda: SimpleNamespace(
        frontmostApplication=lambda: FakeApp()))
    monkeypatch.setattr(appctx, "_workspace", lambda: ws)
    assert appctx._mac() == appctx.AppInfo("com.apple.terminal", "terminal", "terminal")


def test_mac_without_pyobjc_falls_back_to_lsappinfo(monkeypatch):
    monkeypatch.setattr(appctx, "_workspace", lambda: None)
    fake_run(monkeypatch, {
        ("lsappinfo", "front"): "ASN:0x0-0x1a01a:\n",
        ("lsappinfo", "info"): '"CFBundleIdentifier"="com.googlecode.iterm2"\n',
    })
    assert appctx._mac() == appctx.AppInfo("com.googlecode.iterm2", "", "")


def test_mac_failure_is_unknown(monkeypatch):
    def boom():
        raise RuntimeError("no window server")
    ws = SimpleNamespace(sharedWorkspace=boom)
    monkeypatch.setattr(appctx, "_workspace", lambda: ws)
    assert appctx._mac() is appctx.UNKNOWN
    assert appctx._lsappinfo_value('"CFBundleIdentifier"=[ NULL ]') == ""


# ---------------------------------------------------------------- Dispatch
def test_active_app_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    for name in ("_mac", "_windows", "_linux"):
        monkeypatch.setattr(appctx, name, boom)
    assert appctx.active_app() is appctx.UNKNOWN


def test_windows_path_is_unknown_off_windows():
    """Auf anderen Systemen fehlt user32: kein Absturz, nur unbekannt."""
    if os.name == "nt":
        return
    assert appctx._windows() is appctx.UNKNOWN


def test_live_query_on_this_machine_is_fast():
    """Echte Abfrage, ohne Fenster zu verändern: < 20 ms nach dem ersten
    Aufruf (der erste lädt unter macOS einmalig AppKit)."""
    import time
    appctx.active_app()
    t = time.perf_counter()
    app = appctx.active_app()
    assert isinstance(app, appctx.AppInfo)
    if sys.platform == "darwin" or os.name == "nt":
        assert time.perf_counter() - t < 0.02


def test_tests_never_read_the_real_profiles_file():
    """OPEN 4: conftest.py lenkt profiles.ini für jeden Test um."""
    from quassel import config, profiles
    assert not profiles.PROFILES.startswith(config.CONFDIR)
    assert not os.path.exists(profiles.PROFILES)
