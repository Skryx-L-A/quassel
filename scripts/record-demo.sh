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

echo "==> Aktiven Monitor ermitteln…"
GEO=$(kscreen-doctor -j | python3 - <<'PY'
import json, subprocess, sys
data = json.load(sys.stdin)
# Aktiven Output über die VoxType-Pille erfragen wäre zirkulär — nimm den
# Monitor mit dem Cursor: KWin legt die Pille dorthin, wo das aktive Fenster
# ist; nach 3 s Inaktivität ist das der Monitor des zuletzt aktiven Fensters.
# Fallback: erster aktiver Output.
outs = [o for o in data["outputs"] if o.get("enabled")]
o = outs[0]
p, s = o["pos"], o["size"]
print(p["x"], p["y"], s["width"], s["height"])
PY
)
read -r MX MY MW MH <<<"$GEO"
CROP_W=1000; CROP_H=300
CROP_X=$((MX + MW / 2 - CROP_W / 2))
CROP_Y=$((MY + MH - CROP_H))
echo "    Ausschnitt: ${CROP_W}x${CROP_H}+${CROP_X}+${CROP_Y}"

echo "==> Virtuelles Mikrofon einrichten…"
MOD=$(pactl load-module module-null-sink sink_name=voxdemo sink_properties=device.description=VoxTypeDemo)
pactl set-default-source voxdemo.monitor

espeak-ng -v de+f3 -s 145 -w "$TMP/tts.wav" \
    "Hallo, das ist Vox Type. Ich spreche einfach, und der Text erscheint sofort dort wo mein Cursor ist."

echo "==> Editor öffnen (Ziel fürs Diktat)…"
kwrite --new "$TMP/demo.txt" >/dev/null 2>&1 & KPID=$!
sleep 3

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
    -resize 700x -delay 45 -loop 0 -layers Optimize assets/screenshots/demo.gif
echo "    -> assets/screenshots/demo.gif ($(du -h assets/screenshots/demo.gif | cut -f1))"
echo
echo "Fertig! GIF prüfen (keine privaten Inhalte am unteren Bildschirmrand?),"
echo "dann committen: git add assets/screenshots/demo.gif && git commit"
