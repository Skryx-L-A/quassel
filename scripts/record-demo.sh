#!/usr/bin/env bash
# ============================================================================
# Demo-GIF für das README aufnehmen — vollautomatisch (KDE Wayland).
#
#   ./scripts/record-demo.sh
#
# WICHTIG: Nach dem Start ~30 Sekunden NICHT Maus/Tastatur anfassen und
# vorher private Fenster auf dem aktiven Monitor schließen — es werden
# Bildschirmfotos des unteren Bildschirmrands gemacht (Pillen-Bereich).
#
# Ablauf: virtuelles Mikrofon -> TTS-Stimme „diktiert" -> VoxType zeigt
# Live-Transkript in der Pille -> Frames -> assets/screenshots/demo.gif.
# Das Standard-Mikrofon wird danach wiederhergestellt.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

command -v espeak-ng >/dev/null || { echo "espeak-ng fehlt (sudo dnf install espeak-ng)"; exit 1; }
command -v spectacle >/dev/null || { echo "spectacle fehlt"; exit 1; }
command -v magick >/dev/null || { echo "ImageMagick fehlt"; exit 1; }
systemctl --user is-active --quiet voxtyped || { echo "VoxType erst einschalten!"; exit 1; }

TMP=$(mktemp -d)
OLD_SRC=$(pactl get-default-source)
MOD=""
KPID=""
cleanup() {
    [[ -n "$KPID" ]] && kill -9 "$KPID" 2>/dev/null || true
    pactl set-default-source "$OLD_SRC" 2>/dev/null || true
    [[ -n "$MOD" ]] && pactl unload-module "$MOD" 2>/dev/null || true
    rm -rf "$TMP"
}
trap cleanup EXIT

echo "==> Pillen-Monitor direkt von der Pille erfragen…"
PILL_OUT=$(busctl --user call io.github.skryx.voxtype.Pill / io.github.skryx.voxtype.Pill GetActiveOutput 2>/dev/null | sed 's/^s "//; s/"$//')
echo "    Pille meldet: ${PILL_OUT:-unbekannt}"
GEO=$(PILL_OUT="$PILL_OUT" python3 - <<'PY'
import json, subprocess
from gi.repository import Gio, GLib

# Aktiven Monitor von KWin erfragen (gleicher Mechanismus wie die Pille)
result = {}
loop = GLib.MainLoop()
XML = ("<node><interface name='io.github.skryx.voxtype.DemoGeo'>"
       "<method name='Set'><arg type='s' name='o' direction='in'/></method>"
       "</interface></node>")

def on_call(conn, sender, path, iface, method, params, inv):
    result["name"] = params[0]; inv.return_value(None); loop.quit()

def on_bus(conn, name):
    conn.register_object("/", Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], on_call)
    open("/tmp/voxdemo-geo.js", "w").write(
        'callDBus("io.github.skryx.voxtype.DemoGeo","/",'
        '"io.github.skryx.voxtype.DemoGeo","Set",workspace.activeScreen.name);')
    for args in (["unloadScript", "s", "voxdemo-geo"],
                 ["loadScript", "ss", "/tmp/voxdemo-geo.js", "voxdemo-geo"],
                 ["start"]):
        subprocess.run(["busctl", "--user", "call", "org.kde.KWin", "/Scripting",
                        "org.kde.kwin.Scripting", *args], capture_output=True)

Gio.bus_own_name(Gio.BusType.SESSION, "io.github.skryx.voxtype.DemoGeo",
                 Gio.BusNameOwnerFlags.NONE, on_bus, None, None)
GLib.timeout_add(4000, loop.quit)
loop.run()
subprocess.run(["busctl", "--user", "call", "org.kde.KWin", "/Scripting",
                "org.kde.kwin.Scripting", "unloadScript", "s", "voxdemo-geo"],
               capture_output=True)

outs = json.loads(subprocess.run(["kscreen-doctor", "-j"], capture_output=True,
                                 text=True).stdout)["outputs"]
outs = [o for o in outs if o.get("enabled")]
import os
pill = os.environ.get("PILL_OUT", "")
o = next((x for x in outs if x["name"] == pill),
         next((x for x in outs if x["name"] == result.get("name")), outs[0]))
p, s = o["pos"], o["size"]
print(p["x"], p["y"], s["width"], s["height"])
PY
)
read -r MX MY MW MH <<<"$GEO"
CROP_W=760; CROP_H=240
CROP_X=$((MX + MW / 2 - CROP_W / 2))
CROP_Y=$((MY + MH - CROP_H))
echo "    Ausschnitt: ${CROP_W}x${CROP_H}+${CROP_X}+${CROP_Y}"

echo "==> Virtuelles Mikrofon einrichten…"
MOD=$(pactl load-module module-null-sink sink_name=voxdemo sink_properties=device.description=VoxTypeDemo)
pactl set-default-source voxdemo.monitor

espeak-ng -v en-us+f3 -s 150 -w "$TMP/tts.wav" \
    "Hello! I just speak, and the text instantly appears right where my cursor is."

echo "==> Diktat geht in DEIN fokussiertes Fenster — gleich geht es los…"
sleep 2

echo "==> Aufnahme läuft — bitte NICHT eingreifen…"
mkdir -p "$TMP/frames"
( for i in $(seq -w 1 18); do
      spectacle -b -n -f -o "$TMP/frames/f$i.png" >/dev/null 2>&1
      sleep 0.35
  done ) & SHOTS=$!

export YDOTOOL_SOCKET="${YDOTOOL_SOCKET:-/tmp/.ydotool_socket}"
[[ -S /tmp/.ydotool_socket ]] || YDOTOOL_SOCKET="$XDG_RUNTIME_DIR/.ydotool_socket"
sleep 0.5
ydotool key 125:1 29:1          # Strg+Meta halten
paplay -d voxdemo "$TMP/tts.wav"
sleep 0.3
ydotool key 29:0 125:0          # loslassen -> einfügen
wait $SHOTS
sleep 1

echo "==> GIF bauen…"
mkdir -p assets/screenshots
magick "$TMP"/frames/f*.png -crop "${CROP_W}x${CROP_H}+${CROP_X}+${CROP_Y}" +repage \
    -delay 45 -loop 0 -layers Optimize assets/screenshots/demo.gif
echo "    -> assets/screenshots/demo.gif ($(du -h assets/screenshots/demo.gif | cut -f1))"
echo
echo "Fertig! GIF prüfen (keine privaten Inhalte am unteren Bildschirmrand?),"
echo "dann committen: git add assets/screenshots/demo.gif && git commit"
