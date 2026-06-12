"""Linux-Adapter: Einfügen (Clipboard + ydotool Shift+Einfg) und Tasten senden.

Das Einfügen über die Zwischenablage ist layout-sicher (QWERTZ inkl.
Umlauten) und funktioniert auch in Terminals.
"""
import os
import subprocess
import threading
import time

KEY_BACKSPACE = 14

XDG_RUNTIME = os.environ.get("XDG_RUNTIME_DIR",
    f"/run/user/{os.getuid()}" if hasattr(os, "getuid") else "")
IS_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY")) or \
    os.environ.get("XDG_SESSION_TYPE", "") == "wayland"
if IS_WAYLAND:
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")


def find_ydotool_socket():
    if os.environ.get("YDOTOOL_SOCKET"):
        return os.environ["YDOTOOL_SOCKET"]
    for cand in (os.path.join(XDG_RUNTIME, ".ydotool_socket"),
                 "/tmp/.ydotool_socket"):
        if os.path.exists(cand):
            return cand
    return os.path.join(XDG_RUNTIME, ".ydotool_socket")


os.environ["YDOTOOL_SOCKET"] = find_ydotool_socket()


def clip_copy(text):
    if IS_WAYLAND:
        subprocess.run(["wl-copy"], input=text, text=True, check=False)
        subprocess.run(["wl-copy", "--primary"], input=text, text=True, check=False)
    else:
        for sel in ("clipboard", "primary"):
            subprocess.run(["xclip", "-selection", sel], input=text, text=True,
                           check=False)


def clip_read():
    cmd = (["wl-paste", "--no-newline", "-t", "text"] if IS_WAYLAND
           else ["xclip", "-selection", "clipboard", "-o"])
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else ""


def paste(text):
    old = clip_read()
    clip_copy(text)
    time.sleep(0.25)
    # Shift+Einfg: KEY_LEFTSHIFT=42, KEY_INSERT=110
    subprocess.run(["ydotool", "key", "42:1", "110:1", "110:0", "42:0"], check=False)
    if old:
        def restore():
            # großzügig warten: XWayland-Fenster holen den Clipboard-Inhalt
            # teils verzögert ab — zu frühes Wiederherstellen fügt sonst den
            # ALTEN Inhalt ein
            time.sleep(6)
            clip_copy(old)
        threading.Thread(target=restore, daemon=True).start()


def type_chunk(text):
    """Streaming-Häppchen einfügen, OHNE die Zwischenablage zu restaurieren
    (das macht streaming_restore() einmal am Diktatende)."""
    if not text:
        return
    clip_copy(text)
    time.sleep(0.12)
    subprocess.run(["ydotool", "key", "42:1", "110:1", "110:0", "42:0"], check=False)


def streaming_begin():
    """Zwischenablage vor dem Streaming sichern."""
    return clip_read()


def streaming_restore(old):
    if old:
        def restore():
            time.sleep(2)
            clip_copy(old)
        threading.Thread(target=restore, daemon=True).start()


def send_backspaces(n):
    n = min(n, 4000)
    keys = []
    for _ in range(n):
        keys += [f"{KEY_BACKSPACE}:1", f"{KEY_BACKSPACE}:0"]
    while keys:
        subprocess.run(["ydotool", "key"] + keys[:400], check=False)
        keys = keys[400:]


def notify(text, ms=4000):
    subprocess.run(
        ["notify-send", "-a", "VoxType", "-t", str(ms),
         "-h", "string:x-canonical-private-synchronous:voxtype", "VoxType", text],
        check=False)
