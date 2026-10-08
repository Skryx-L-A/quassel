"""Welche Anwendung hat gerade den Fokus? (Audit G102, Per-App-Profile)

active_app() liefert eine AppInfo mit Kennungen der Vordergrund-Anwendung,
plattformgerecht und ohne neue Pflichtabhängigkeit:

  macOS    NSWorkspace.frontmostApplication (PyObjC, ohnehin Abhängigkeit);
           ohne PyObjC `lsappinfo`
  Windows  GetForegroundWindow + Prozessname + Fensterklasse über ctypes
  X11      _NET_ACTIVE_WINDOW über xprop (WM_CLASS, _NET_WM_PID)
  Wayland  Hyprland (hyprctl activewindow -j), Sway (swaymsg -t get_tree);
           GNOME und KDE geben die aktive Anwendung ohne Erweiterung nicht
           heraus -> unbekannt

Datenschutz: Fenstertitel werden nie gelesen, gespeichert oder geloggt —
in ihnen stehen Dokumentnamen, Mail-Betreffs, Chat-Partner. Verwendet wird
nur, was eine Anwendung benennt: Bundle-ID, Prozessname, Fensterklasse.

Jede Abfrage ist kurz befristet (QUERY_TIMEOUT); scheitert sie, kommt
UNKNOWN zurück und das Diktat läuft ohne Profil weiter. Nie eine Ausnahme.
"""
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass

# Frist je Hilfsprogramm. Üblich sind wenige Millisekunden; die Frist fängt
# nur einen hängenden Compositor oder X-Server ab.
QUERY_TIMEOUT = 0.25


@dataclass(frozen=True)
class AppInfo:
    """Kennungen der aktiven Anwendung, alle klein geschrieben, "" = unbekannt.

    bundle_id  macOS-Bundle-ID (com.apple.Terminal)
    process    Prozessname ohne Pfad (code.exe, kitty, Terminal)
    wm_class   Fensterklasse: X11 WM_CLASS, Wayland app_id, Windows-Klassenname;
               auf macOS der Anwendungsname (Terminal, Mail)
    """
    bundle_id: str = ""
    process: str = ""
    wm_class: str = ""

    def identifiers(self):
        """Alle bekannten Kennungen, ohne Dubletten, in fester Reihenfolge."""
        seen = []
        for value in (self.bundle_id, self.process, self.wm_class):
            if value and value not in seen:
                seen.append(value)
        return tuple(seen)

    @property
    def known(self):
        return bool(self.identifiers())

    def label(self):
        """Kurzname fürs Log: eine Kennung, nie ein Titel."""
        ids = self.identifiers()
        return ids[0] if ids else "unknown"


UNKNOWN = AppInfo()


def _norm(value):
    return str(value or "").strip().lower()


def make(bundle_id="", process="", wm_class=""):
    process = _norm(process)
    if process:
        process = os.path.basename(process.replace("\\", "/"))
    return AppInfo(_norm(bundle_id), process, _norm(wm_class))


def _run(argv):
    """Hilfsprogramm mit Frist; -> stdout oder None."""
    try:
        res = subprocess.run(argv, capture_output=True, text=True,
                             timeout=QUERY_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def _proc_name(pid):
    """Linux: Prozessname zur PID aus /proc (ohne Kommandozeile)."""
    try:
        pid = int(pid)
        if pid <= 0:
            return ""
        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except (OSError, ValueError, TypeError):
        return ""


# ------------------------------------------------------------------ macOS
_appkit = None
_appkit_lock = threading.Lock()


def _workspace():
    global _appkit
    with _appkit_lock:
        if _appkit is None:
            try:
                from AppKit import NSWorkspace
                _appkit = NSWorkspace
            except Exception:
                _appkit = False
    return _appkit or None


def prewarm():
    """macOS: AppKit im Hintergrund laden. Der Import kostet einmalig etwa
    90 ms; ohne Vorwärmen fielen sie in das erste Diktat. Anderswo nichts.
    -> der gestartete Thread oder None."""
    if sys.platform != "darwin":
        return None
    t = threading.Thread(target=_workspace, name="appctx-prewarm", daemon=True)
    t.start()
    return t


def _mac():
    ws = _workspace()
    if ws is not None:
        try:
            app = ws.sharedWorkspace().frontmostApplication()
            if app is None:
                return UNKNOWN
            exe = app.executableURL()
            return make(app.bundleIdentifier() or "",
                        exe.lastPathComponent() if exe is not None else "",
                        app.localizedName() or "")
        except Exception:
            return UNKNOWN
    return _mac_lsappinfo()


def _mac_lsappinfo():
    asn = _run(["lsappinfo", "front"])
    if not asn or not asn.strip():
        return UNKNOWN
    out = _run(["lsappinfo", "info", "-only", "bundleid", asn.strip()])
    return make(bundle_id=_lsappinfo_value(out))


def _lsappinfo_value(out):
    """'"CFBundleIdentifier"="com.apple.Terminal"' -> 'com.apple.Terminal'."""
    if not out or "=" not in out:
        return ""
    value = out.split("=", 1)[1].strip().strip('"')
    return "" if value in ("", "[ NULL ]") else value


# ---------------------------------------------------------------- Windows
def _windows():
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return UNKNOWN
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        if pid.value:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                          False, pid.value)
            if handle:
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                           ctypes.byref(size)):
                        exe = buf.value
                finally:
                    kernel32.CloseHandle(handle)
        return make(process=exe, wm_class=cls.value)
    except Exception:
        return UNKNOWN


# ------------------------------------------------------------------ Linux
def _x11():
    out = _run(["xprop", "-root", "-notype", "_NET_ACTIVE_WINDOW"])
    wid = _xprop_window_id(out)
    if not wid:
        return UNKNOWN
    # Nur WM_CLASS und PID anfragen — WM_NAME (Titel) bewusst nicht.
    out = _run(["xprop", "-id", wid, "-notype", "WM_CLASS", "_NET_WM_PID"])
    wm_class, pid = _xprop_class_pid(out)
    return make(process=_proc_name(pid), wm_class=wm_class)


def _xprop_window_id(out):
    """'_NET_ACTIVE_WINDOW: window id # 0x3e00007' -> '0x3e00007'."""
    if not out or "#" not in out:
        return ""
    wid = out.rsplit("#", 1)[1].strip().split(",")[0].strip()
    try:
        return wid if wid.startswith("0x") and int(wid, 16) != 0 else ""
    except ValueError:
        return ""


def _xprop_class_pid(out):
    """WM_CLASS = "instanz", "Klasse" -> Klasse; _NET_WM_PID = 1234 -> 1234."""
    wm_class, pid = "", 0
    for line in (out or "").splitlines():
        key, _, value = line.partition("=")
        key = key.strip()
        if key == "WM_CLASS":
            parts = [p.strip().strip('"') for p in value.split(",")]
            parts = [p for p in parts if p]
            wm_class = parts[-1] if parts else ""
        elif key == "_NET_WM_PID":
            try:
                pid = int(value.strip())
            except ValueError:
                pid = 0
    return wm_class, pid


def _hyprland():
    out = _run(["hyprctl", "activewindow", "-j"])
    try:
        data = json.loads(out or "")
    except ValueError:
        return UNKNOWN
    if not isinstance(data, dict):
        return UNKNOWN
    return make(process=_proc_name(data.get("pid")),
                wm_class=data.get("class") or data.get("initialClass") or "")


def _sway():
    out = _run(["swaymsg", "-t", "get_tree", "-r"])
    try:
        tree = json.loads(out or "")
    except ValueError:
        return UNKNOWN
    node = _sway_focused(tree)
    if node is None:
        return UNKNOWN
    props = node.get("window_properties") or {}
    return make(process=_proc_name(node.get("pid")),
                wm_class=node.get("app_id") or props.get("class") or "")


def _sway_focused(node):
    """Fokussiertes Fenster im Sway-Baum (iterativ, ohne Rekursionsgrenze)."""
    stack = [node] if isinstance(node, dict) else []
    while stack:
        cur = stack.pop()
        if cur.get("focused") and cur.get("type") in ("con", "floating_con") \
                and (cur.get("app_id") or cur.get("window_properties") or cur.get("pid")):
            return cur
        for key in ("nodes", "floating_nodes"):
            children = cur.get(key) or []
            stack.extend(c for c in children if isinstance(c, dict))
    return None


def _linux(env=None):
    env = os.environ if env is None else env
    if env.get("WAYLAND_DISPLAY"):
        if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
            return _hyprland()
        if env.get("SWAYSOCK"):
            return _sway()
        # GNOME/KDE unter Wayland: ohne Shell-Erweiterung bzw. KWin-Skript
        # kein Zugriff. xprop sähe nur XWayland-Fenster und meldete sonst
        # still das zuletzt aktive X-Fenster — lieber ehrlich unbekannt.
        return UNKNOWN
    if env.get("DISPLAY"):
        return _x11()
    return UNKNOWN


def active_app():
    """AppInfo der Vordergrund-Anwendung; UNKNOWN, wenn nicht ermittelbar."""
    try:
        if sys.platform == "darwin":
            return _mac()
        if os.name == "nt":
            return _windows()
        return _linux()
    except Exception:
        return UNKNOWN
