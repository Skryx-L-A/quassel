"""Linux-Adapter: Einfügen (Clipboard + ydotool Shift+Einfg) und Tasten senden.

Das Einfügen über die Zwischenablage ist layout-sicher (QWERTZ inkl.
Umlauten) und funktioniert auch in Terminals.
"""
import os
import shutil
import subprocess
import threading
import time
import stat
from types import SimpleNamespace

KEY_BACKSPACE = 14
KEY_ENTER = 28
SUBPROCESS_TIMEOUT = 3


def _run(args, **kwargs):
    """Bounded subprocess call: a stalled clipboard/tool must not hang dictation."""
    try:
        return subprocess.run(args, timeout=SUBPROCESS_TIMEOUT, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return SimpleNamespace(returncode=1, stdout="", stderr="")

XDG_RUNTIME = os.environ.get("XDG_RUNTIME_DIR",
    f"/run/user/{os.getuid()}" if hasattr(os, "getuid") else "")
IS_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY")) or \
    os.environ.get("XDG_SESSION_TYPE", "") == "wayland"
if IS_WAYLAND and not os.environ.get("WAYLAND_DISPLAY") and XDG_RUNTIME:
    # The session name is not fixed; guessing can point at another session.
    try:
        displays = [name for name in os.listdir(XDG_RUNTIME)
                    if name.startswith("wayland-") and name[8:].isdigit()
                    and stat.S_ISSOCK(os.stat(os.path.join(XDG_RUNTIME, name)).st_mode)]
    except OSError:
        displays = []
    if len(displays) == 1:
        os.environ["WAYLAND_DISPLAY"] = displays[0]


# Was der Dienst von der grafischen Sitzung wissen muss, um dort einzufügen.
SESSION_VARS = ("WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE")


def import_session_env(run=_run):
    """Vor dem Start von quasseld der systemd-User-Instanz die Anzeige dieser
    Sitzung mitgeben. GNOME und KDE tun das selbst, Sway/Hyprland ohne
    dbus-update-activation-environment nicht; der Dienst wüsste dann nicht,
    wohin er tippt. Nur tatsächlich gesetzte Werte, nie ein geratenes wayland-0."""
    names = [n for n in SESSION_VARS if os.environ.get(n)]
    if names:
        run(["systemctl", "--user", "import-environment", *names], check=False)
    return names


def find_ydotool_socket():
    """Return only an owned socket below this user's runtime directory.

    A socket in /tmp is shared between local users and must never become an
    input-injection endpoint merely because ydotoold is missing.
    """
    runtime = os.path.realpath(XDG_RUNTIME) if XDG_RUNTIME else ""
    candidate = os.environ.get("YDOTOOL_SOCKET") or os.path.join(runtime, ".ydotool_socket")
    if not runtime or not candidate:
        return None
    try:
        candidate = os.path.realpath(candidate)
        if os.path.commonpath((runtime, candidate)) != runtime:
            return None
        info = os.stat(candidate)
        if info.st_uid != os.getuid() or not stat.S_ISSOCK(info.st_mode):
            return None
    except (OSError, ValueError):
        return None
    return candidate


_ydotool_socket = find_ydotool_socket()
if _ydotool_socket:
    os.environ["YDOTOOL_SOCKET"] = _ydotool_socket
else:
    os.environ.pop("YDOTOOL_SOCKET", None)


_sensitive_supported = None


def _sensitive_copy_supported():
    """wl-copy 2.3+ advertises the KDE password-manager MIME hint."""
    global _sensitive_supported
    if not IS_WAYLAND:
        return False
    if _sensitive_supported is None:
        result = _run(["wl-copy", "--help"], capture_output=True, text=True, check=False)
        _sensitive_supported = (result.returncode == 0 and
                                "--sensitive" in (result.stdout + result.stderr))
    return _sensitive_supported


def clip_copy(text, private=False):
    if IS_WAYLAND:
        args = ["wl-copy", "--sensitive"] if private and _sensitive_copy_supported() else ["wl-copy"]
        return _run(args, input=text, text=True, check=False).returncode == 0
    else:
        return _run(["xclip", "-selection", "clipboard"], input=text,
                    text=True, check=False).returncode == 0


def clip_read():
    cmd = (["wl-paste", "--no-newline", "-t", "text"] if IS_WAYLAND
           else ["xclip", "-selection", "clipboard", "-o"])
    r = _run(cmd, capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else ""


_clip_lock = threading.Lock()
_original = None
_last_written = None
_generation = 0
_prepared = None
_prepare_id = 0
_TEXT_TYPES = {"text", "TEXT", "STRING", "UTF8_STRING", "COMPOUND_TEXT",
               "text/plain", "text/plain;charset=utf-8", "TARGETS", "TIMESTAMP", "MULTIPLE"}


def _snapshot():
    """Read clipboard type list before changing it; None means unsafe/unknown."""
    args = (["wl-paste", "--list-types"] if IS_WAYLAND else
            ["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"])
    result = _run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    types = {value.strip() for value in result.stdout.splitlines() if value.strip()}
    if not types or not types <= _TEXT_TYPES:
        return None
    return clip_read()


def prepare_clipboard():
    """Start bounded snapshot during recording, before transcription finishes."""
    global _prepare_id, _prepared
    with _clip_lock:
        _prepare_id += 1
        _prepared = None
        generation = _prepare_id

    def read():
        global _prepared
        snapshot = _snapshot()
        with _clip_lock:
            if generation == _prepare_id:
                _prepared = snapshot

    threading.Thread(target=read, daemon=True).start()


def _begin():
    global _original, _prepared, _last_written
    with _clip_lock:
        if _original is not None:
            if _last_written is None or clip_read() == _last_written:
                return _original
            _original = None
            _last_written = None
        saved = _prepared
        _prepared = None
    if saved is None:
        saved = _snapshot()
    else:
        # A user may copy something while recording: take the new item.
        current = clip_read()
        if current != saved:
            saved = _snapshot()
    with _clip_lock:
        _original = saved
    return saved


def _type_direct(text):
    # stdin keeps dictation out of process arguments and command logs.
    # ydotool uses a fixed US ASCII key map. Other layouts or UTF-8 can
    # silently produce different text even when the command succeeds.
    if not text.isascii() or os.environ.get("XKB_DEFAULT_LAYOUT") != "us":
        return False
    return _run(["ydotool", "type", "--file", "-"], input=text,
                text=True, check=False).returncode == 0


def _restore_if_ours(delay, generation):
    global _original, _last_written
    time.sleep(delay)
    with _clip_lock:
        if generation != _generation:
            return
        old, written = _original, _last_written
        _original = None
        _last_written = None
        if old is not None and written is not None and clip_read() == written:
            clip_copy(old)


def paste(text):
    global _last_written, _generation
    old = _begin()
    if old is None:
        if _type_direct(text):
            return True
        notify("Clipboard format cannot be preserved; dictation was not inserted")
        return False
    if not clip_copy(text, private=True):
        return False
    with _clip_lock:
        _last_written = text
        _generation += 1
        generation = _generation
    time.sleep(0.25)
    # Shift+Einfg: KEY_LEFTSHIFT=42, KEY_INSERT=110
    result = _run(["ydotool", "key", "42:1", "110:1", "110:0", "42:0"], check=False)
    if result.returncode == 0:
        threading.Thread(target=_restore_if_ours, args=(6, generation), daemon=True).start()
    return result.returncode == 0


def type_chunk(text):
    """Streaming-Häppchen einfügen, OHNE die Zwischenablage zu restaurieren
    (das macht streaming_restore() einmal am Diktatende)."""
    if not text:
        return
    global _last_written, _generation
    if _begin() is None:
        if not _type_direct(text):
            notify("Clipboard format cannot be preserved; dictation was not inserted")
            return False
        return True
    if not clip_copy(text, private=True):
        return False
    with _clip_lock:
        _last_written = text
        _generation += 1
    time.sleep(0.12)
    return _run(["ydotool", "key", "42:1", "110:1", "110:0", "42:0"], check=False).returncode == 0


def streaming_begin():
    """Zwischenablage vor dem Streaming sichern."""
    return _begin()


def streaming_restore(old):
    if old is not None:
        with _clip_lock:
            generation = _generation
        threading.Thread(target=_restore_if_ours, args=(2, generation), daemon=True).start()


def send_backspaces(n):
    n = min(n, 4000)
    keys = []
    for _ in range(n):
        keys += [f"{KEY_BACKSPACE}:1", f"{KEY_BACKSPACE}:0"]
    delay = ["-d", "1"] if _ydotool_supports_fast_delay() else []
    while keys:
        _run(["ydotool", "key"] + delay + keys[:400], check=False)
        keys = keys[400:]


_fast_delay_supported = None


def _ydotool_supports_fast_delay():
    """Detect -d once; older ydotool versions retain the safe old command."""
    global _fast_delay_supported
    if _fast_delay_supported is None:
        try:
            result = _run(["ydotool", "key", "--help"],
                                    capture_output=True, text=True, timeout=2,
                                    check=False)
            _fast_delay_supported = "-d" in result.stdout or "--delay" in result.stdout
        except (OSError, subprocess.SubprocessError):
            _fast_delay_supported = False
    return _fast_delay_supported


def send_enter():
    """Eingabetaste drücken (Sprachkommando 'press enter')."""
    _run(["ydotool", "key", f"{KEY_ENTER}:1", f"{KEY_ENTER}:0"], check=False)


def mic_is_bluetooth(mic="default"):
    """True, wenn die aktive Aufnahmequelle ein Bluetooth-Gerät ist (bluez).

    BT-Headsets/Earbuds (AirPods etc.) schalten beim Mikrofonstart von A2DP auf
    HFP — das kostet Zeit (Anfang wird abgeschnitten) und liefert mageren Ton.
    Wird genutzt, um Vorlauf/Nachlauf großzügiger zu wählen."""
    try:
        if mic and mic != "default":
            return "bluez" in mic.lower()
        r = _run(["pactl", "get-default-source"], capture_output=True,
                           text=True, timeout=2, check=False)
        return "bluez" in r.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def notify(text, ms=4000):
    _run(
        ["notify-send", "-a", "Quassel", "-t", str(ms),
         "-h", "string:x-canonical-private-synchronous:quassel", "Quassel", text],
        check=False)


# ---------------------------------------------------------- Audio-Ducking
# Backend für quassel.mediacontrol: Master-Ton stummschalten (pactl) bzw.
# spielende MPRIS-Player pausieren (playerctl, sonst busctl/systemd).
def _pactl(*args):
    # LC_ALL=C: erzwingt englische Ausgabe ("Mute: yes/no") — sonst liest die
    # Mute-Erkennung auf z.B. deutschen Systemen ("Mute: ja/nein") falsch.
    return _run(["pactl", *args], capture_output=True, text=True,
                          check=False, env={**os.environ, "LC_ALL": "C"})


def _default_sink_muted():
    return "yes" in _pactl("get-sink-mute", "@DEFAULT_SINK@").stdout.lower()


def _mpris_playing():
    """Liste der gerade spielenden Player als (backend, name)-Paare."""
    if shutil.which("playerctl"):
        names = _run(["playerctl", "-l"], capture_output=True,
                               text=True, check=False).stdout.split()
        out = []
        for n in names:
            s = _run(["playerctl", "-p", n, "status"], capture_output=True,
                               text=True, check=False).stdout.strip()
            if s == "Playing":
                out.append(("playerctl", n))
        return out
    if shutil.which("busctl"):
        listing = _run(["busctl", "--user", "list", "--no-legend"],
                                 capture_output=True, text=True, check=False).stdout
        svcs = [ln.split()[0] for ln in listing.splitlines()
                if ln.startswith("org.mpris.MediaPlayer2.")]
        out = []
        for svc in svcs:
            r = _run(["busctl", "--user", "get-property", svc,
                                "/org/mpris/MediaPlayer2",
                                "org.mpris.MediaPlayer2.Player", "PlaybackStatus"],
                               capture_output=True, text=True, check=False)
            if '"Playing"' in r.stdout:
                out.append(("busctl", svc))
        return out
    return []


def _mpris_call(item, method):
    backend, name = item
    if backend == "playerctl":
        _run(["playerctl", "-p", name,
                        "pause" if method == "Pause" else "play"], check=False)
    else:
        _run(["busctl", "--user", "call", name, "/org/mpris/MediaPlayer2",
                        "org.mpris.MediaPlayer2.Player", method], check=False)


def duck_apply(mode):
    """'all' -> Master-Sink stumm; 'music' -> spielende Player pausieren.
    Rückgabe: Token, das duck_restore unverändert erhält."""
    if mode == "all":
        if not shutil.which("pactl"):
            return None
        token = {"was_muted": _default_sink_muted()}
        _pactl("set-sink-mute", "@DEFAULT_SINK@", "1")
        return token
    if mode == "music":
        players = _mpris_playing()
        for p in players:
            _mpris_call(p, "Pause")
        return {"players": players}
    return None


def duck_restore(mode, token):
    if not token:
        return
    if mode == "all":
        # nur entstummen, wenn der Ton vor dem Diktat NICHT stumm war
        if not token.get("was_muted") and shutil.which("pactl"):
            _pactl("set-sink-mute", "@DEFAULT_SINK@", "0")
    elif mode == "music":
        for p in token.get("players", []):
            _mpris_call(p, "Play")
