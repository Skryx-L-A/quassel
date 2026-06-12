#!/usr/bin/env bash
# ============================================================================
# Demo-GIF für das README aufnehmen — vollautomatisch (KDE Wayland).
#
#   ./scripts/record-demo.sh
#
# WICHTIG: Nach dem Start ~35 Sekunden NICHT Maus/Tastatur anfassen.
#
# Ablauf: virtuelles Mikrofon -> TTS-Stimme „diktiert" -> Quassel zeigt
# Live-Transkript in der Pille und fügt den Text in einen maximierten
# Editor ein. Das GIF kombiniert zwei Streifen aus demselben Bildschirmfoto:
# oben die erste Editorzeile (der eingefügte Text), unten die Pille.
# Das Standard-Mikrofon wird danach wiederhergestellt.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

for c in espeak-ng spectacle magick kwrite; do
    command -v "$c" >/dev/null || { echo "$c fehlt"; exit 1; }
done
systemctl --user is-active --quiet quasseld || { echo "Quassel erst einschalten!"; exit 1; }

TMP=$(mktemp -d)
OLD_SRC=$(pactl get-default-source)
MOD=""
KPID=""
cleanup() {
    [[ -n "$KPID" ]] && kill -9 "$KPID" 2>/dev/null || true
    pactl set-default-source "$OLD_SRC" 2>/dev/null || true
    [[ -n "$MOD" ]] && pactl unload-module "$MOD" 2>/dev/null || true
    busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting \
        unloadScript s voxdemo-max >/dev/null 2>&1 || true
    rm -rf "$TMP"
}
trap cleanup EXIT

echo "==> Pillen-Monitor erfragen…"
PILL_OUT=$(busctl --user call io.github.skryx.quassel.Pill / \
    io.github.skryx.quassel.Pill GetActiveOutput 2>/dev/null \
    | sed 's/^s "//; s/"$//')
GEO=$(kscreen-doctor -j | PILL_OUT="$PILL_OUT" python3 -c "
import json, os, sys
outs = [o for o in json.load(sys.stdin)['outputs'] if o.get('enabled')]
pill = os.environ.get('PILL_OUT', '')
o = next((x for x in outs if x['name'] == pill), outs[0])
p, s = o['pos'], o['size']
print(p['x'], p['y'], s['width'], s['height'])
")
read -r MX MY MW MH <<<"$GEO"
echo "    Monitor: ${PILL_OUT:-?} (${MW}x${MH}+${MX}+${MY})"

# Streifen: A = erste Editorzeile, B = Pille (+ Live-Text darüber)
A_X=$((MX + 40)); A_Y=$((MY + 108)); A_W=760; A_H=120
B_X=$((MX + MW / 2 - 380)); B_Y=$((MY + MH - 240)); B_W=760; B_H=200

echo "==> Virtuelles Mikrofon einrichten…"
MOD=$(pactl load-module module-null-sink sink_name=voxdemo sink_properties=device.description=QuasselDemo)
pactl set-default-source voxdemo.monitor

espeak-ng -v en-us+f3 -s 150 -w "$TMP/tts.wav" \
    "Hello! I just speak, and the text instantly appears right where my cursor is."

echo "==> Editor öffnen, maximieren, fokussieren (KWin)…"
LANG=en_US.UTF-8 LANGUAGE=en kwrite "$TMP/demo.txt" >/dev/null 2>&1 & KPID=$!
sleep 2.5
cat > "$TMP/max.js" <<'JS'
var list = workspace.windowList();
for (var i = 0; i < list.length; i++) {
    var w = list[i];
    if (w.resourceClass.toString().indexOf("kwrite") !== -1) {
        w.setMaximize(true, true);
        workspace.activeWindow = w;
    }
}
JS
busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting \
    unloadScript s voxdemo-max >/dev/null 2>&1 || true
SID=$(busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting \
    loadScript ss "$TMP/max.js" voxdemo-max | awk '{print $2}')
busctl --user call org.kde.KWin "/Scripting/Script$SID" org.kde.kwin.Script run >/dev/null
sleep 1.5

echo "==> Aufnahme läuft — bitte NICHT eingreifen…"
mkdir -p "$TMP/frames"
( for i in $(seq -w 1 18); do
      spectacle -b -n -f -o "$TMP/frames/f$i.png" >/dev/null 2>&1
      sleep 0.35
  done ) & SHOTS=$!

export YDOTOOL_SOCKET="${YDOTOOL_SOCKET:-/tmp/.ydotool_socket}"
[[ -S "$YDOTOOL_SOCKET" ]] || YDOTOOL_SOCKET="$XDG_RUNTIME_DIR/.ydotool_socket"
sleep 0.5
ydotool key 125:1 29:1          # Strg+Meta halten
paplay -d voxdemo "$TMP/tts.wav"
sleep 0.3
ydotool key 29:0 125:0          # loslassen -> einfügen
wait $SHOTS
sleep 1

echo "==> GIF bauen (zwei Streifen: Editorzeile + Pille)…"
mkdir -p assets/screenshots "$TMP/comp"
for f in "$TMP"/frames/f*.png; do
    n=$(basename "$f")
    magick "$f" \
        \( +clone -crop "${A_W}x${A_H}+${A_X}+${A_Y}" +repage \) \
        \( -clone 0 -crop "${B_W}x${B_H}+${B_X}+${B_Y}" +repage \
           \( +clone -crop "${B_W}x45+0+$((B_H-45))" +repage -blur 0x14 \) \
           -geometry +0+$((B_H-45)) -composite \) \
        -delete 0 -background '#101016' -append "$TMP/comp/$n"
done
magick "$TMP"/comp/f*.png -delay 45 -loop 0 -layers Optimize assets/screenshots/demo.gif
echo "    -> assets/screenshots/demo.gif ($(du -h assets/screenshots/demo.gif | cut -f1))"
echo
echo "Fertig! GIF prüfen, dann committen."
