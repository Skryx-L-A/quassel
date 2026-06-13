#!/usr/bin/env bash
# ============================================================================
#  Quassel — Offline-Installation (portables Linux-Komplettpaket)
#
#  Dieses Paket bringt ALLES mit (Python+Qt, CPU- und CUDA-Engine, alle
#  Modelle, ydotool, Clipboard) — es wird KEIN Internet benötigt.
#
#  Aufruf:  ./install.sh
#  Danach:  App „Quassel" im Startmenü öffnen und einschalten.
#
#  Hinweise:
#   * Einmalig nötig: sudo (für die Tastatur-/uinput-Berechtigung) und ggf.
#     ein erneutes Anmelden, damit die Gruppe 'input' greift.
#   * Das Paket dort lassen, wo es entpackt ist (Pfade werden fest verdrahtet);
#     nach einem Verschieben einfach erneut ./install.sh ausführen.
# ============================================================================
set -euo pipefail
BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$BUNDLE/python/bin/python3"
BIN="$HOME/.local/bin"
UNITS="$HOME/.config/systemd/user"
CONF="$HOME/.config/quassel"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m    OK %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mFEHLER/ERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "Bitte als normaler Benutzer ausführen, NICHT als root."
command -v systemctl >/dev/null || die "Kein systemd (--user) — nicht unterstützt."
[[ -x "$PY" ]] || die "Gebündeltes Python fehlt ($PY) — Paket unvollständig entpackt?"

# ---------------------------------------------------------------------------
say "1/6  Hardware erkennen (Engine + Standard-Modell)"
# ---------------------------------------------------------------------------
if command -v nvidia-smi >/dev/null && [[ -x "$BUNDLE/engine/cuda/whisper-server" ]]; then
    ENGINE="cuda"
else
    ENGINE="cpu"
fi
MODEL="$(PYTHONPATH="$BUNDLE" "$PY" -c \
    'from quassel.hwdetect import default_model_for_hardware as f; print(f())' 2>/dev/null || echo small)"
[[ -s "$BUNDLE/models/ggml-$MODEL.bin" ]] || MODEL="small"
ok "Engine: $ENGINE   Standard-Modell: $MODEL"

# ---------------------------------------------------------------------------
say "2/6  Engine-Wrapper + server.env schreiben"
# ---------------------------------------------------------------------------
# Engine-Wrapper: alle gebündelten Libs (libwhisper/libggml* UND die CUDA-
# Runtime) gelten NUR für den whisper-server-Prozess (LD_LIBRARY_PATH) — am
# System wird nie etwas verändert. Die CUDA-Runtime ist fest an die gebaute
# Version gebunden (das Binary lädt genau libcudart.so.12), daher immer die
# gebündelte nutzen. Der NVIDIA-TREIBER (libcuda.so.1) kommt dagegen IMMER vom
# System und ist vorwärtskompatibel — ein neuerer System-Treiber (CUDA 13/14)
# wird also automatisch verwendet, ohne am System etwas anzufassen.
for v in cpu cuda; do
    [[ -x "$BUNDLE/engine/$v/whisper-server" ]] || continue
    cat > "$BUNDLE/engine/$v/run-server.sh" <<'EOF'
#!/usr/bin/env bash
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$here:${LD_LIBRARY_PATH:-}"
exec "$here/whisper-server" "$@"
EOF
    chmod +x "$BUNDLE/engine/$v/run-server.sh"
done
mkdir -p "$CONF"
cat > "$CONF/server.env" <<EOF
SERVER_BIN=$BUNDLE/engine/$ENGINE/run-server.sh
MODEL_PATH=$BUNDLE/models/ggml-$MODEL.bin
EOF
ok "server.env -> $ENGINE-Engine, Modell $MODEL"

# ---------------------------------------------------------------------------
say "3/6  Launcher (nutzen gebündeltes Python + Tools)"
# ---------------------------------------------------------------------------
mkdir -p "$BIN"
common_env() {
    cat <<EOF
export QUASSEL_BUNDLE="$BUNDLE"
export PYTHONPATH="$BUNDLE"
export PATH="$BUNDLE/tools:\$PATH"
export LD_LIBRARY_PATH="$BUNDLE/tools:\${LD_LIBRARY_PATH:-}"
EOF
}
{ echo '#!/usr/bin/env bash'; common_env; echo "exec \"$PY\" -m quassel.daemon \"\$@\""; } > "$BIN/quasseld"
{ echo '#!/usr/bin/env bash'; common_env
  echo "export QUASSEL_CENTER_CMD=\"$BIN/quassel-type\""
  echo "exec \"$PY\" -m quassel.pill_qt \"\$@\""; } > "$BIN/quassel-pill"
{ echo '#!/usr/bin/env bash'; common_env; echo "exec \"$PY\" -m quassel.center \"\$@\""; } > "$BIN/quassel-type"
{ echo '#!/usr/bin/env bash'
  echo "case \"\${1:-}\" in"
  echo "  start) systemctl --user start quasseld quassel-server ;;"
  echo "  stop)  systemctl --user stop quasseld quassel-server quassel-ydotoold ;;"
  echo "  *) echo 'Aufruf: quassel-ctl start|stop' ;;"
  echo "esac"; } > "$BIN/quassel-ctl"
chmod +x "$BIN/quasseld" "$BIN/quassel-pill" "$BIN/quassel-type" "$BIN/quassel-ctl"
ok "Launcher in $BIN"

# ---------------------------------------------------------------------------
say "4/6  systemd-User-Units (auf das Paket verdrahtet)"
# ---------------------------------------------------------------------------
mkdir -p "$UNITS"
cat > "$UNITS/quassel-server.service" <<EOF
[Unit]
Description=Whisper.cpp speech-to-text server (for Quassel)
[Service]
Type=simple
EnvironmentFile=-%h/.config/quassel/server.env
ExecStart=/bin/sh -c 'exec "\${SERVER_BIN}" -m "\$MODEL_PATH" --host 127.0.0.1 --port 8765 -l auto -nt'
Restart=on-failure
RestartSec=3
EOF
cat > "$UNITS/quassel-ydotoold.service" <<EOF
[Unit]
Description=ydotoold (virtual keyboard for Quassel)
[Service]
Type=simple
ExecStart=$BUNDLE/tools/ydotoold --socket-path=%t/.ydotool_socket
Restart=on-failure
RestartSec=3
EOF
cat > "$UNITS/quasseld.service" <<EOF
[Unit]
Description=Quassel daemon (hotkey detection for voice typing)
Wants=quassel-ydotoold.service quassel-pill.service
After=quassel-ydotoold.service
[Service]
Type=simple
ExecStart=$BIN/quasseld
Restart=on-failure
RestartSec=3
[Install]
WantedBy=default.target
EOF
cat > "$UNITS/quassel-pill.service" <<EOF
[Unit]
Description=Quassel pill overlay
[Service]
Type=simple
ExecStart=$BIN/quassel-pill
Restart=on-failure
RestartSec=3
EOF
systemctl --user daemon-reload
ok "Units installiert"

# ---------------------------------------------------------------------------
say "5/6  Tastatur-Berechtigung (uinput) — einmalig, sudo nötig"
# ---------------------------------------------------------------------------
NEED_RELOGIN=0
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    sudo usermod -aG input "$USER" && NEED_RELOGIN=1 && ok "Benutzer zur Gruppe 'input' hinzugefügt"
else
    ok "Benutzer ist bereits in Gruppe 'input'"
fi
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
    | sudo tee /etc/udev/rules.d/80-quassel-uinput.rules >/dev/null
echo uinput | sudo tee /etc/modules-load.d/quassel-uinput.conf >/dev/null
sudo modprobe uinput 2>/dev/null || true
sudo udevadm control --reload-rules && sudo udevadm trigger /dev/uinput 2>/dev/null || true
ok "udev-Regel für /dev/uinput installiert"

# ---------------------------------------------------------------------------
say "6/6  App-Eintrag + Icon"
# ---------------------------------------------------------------------------
mkdir -p "$APPS" "$ICONS/scalable/apps"
[[ -f "$BUNDLE/quassel/../assets/quassel.svg" ]] && \
    install -m 644 "$BUNDLE/quassel/../assets/quassel.svg" "$ICONS/scalable/apps/quassel.svg" 2>/dev/null || true
cat > "$APPS/quassel.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Quassel
Comment=Local private voice typing / Lokale Spracheingabe
Exec=$BIN/quassel-type
Icon=quassel
Terminal=false
Categories=Utility;AudioVideo;
EOF
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$ICONS" 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
ok "App „Quassel" im Startmenü"

echo
ok "Fertig! Engine=$ENGINE, Modell=$MODEL — KEIN Internet nötig."
[[ "$NEED_RELOGIN" -eq 1 ]] && warn "WICHTIG: einmal ab- und wieder anmelden (Gruppe 'input')!"
cat <<'HOWTO'

    So geht's:
      1. App „Quassel" öffnen und einschalten (oder: quassel-ctl start)
      2. In ein Textfeld klicken:
         - Strg+Meta HALTEN -> sprechen -> loslassen -> Text erscheint
         - Strg+Meta 2x TIPPEN -> freihändig -> 1x drücken -> Text erscheint
    Audio braucht pw-record/parecord (auf jedem Desktop vorhanden).
HOWTO
