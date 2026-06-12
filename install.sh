#!/usr/bin/env bash
# ============================================================================
#  Quassel – Installation (Linux)
#  Lokale, private Spracheingabe: Strg+Meta halten -> sprechen -> loslassen
#  -> Text erscheint am Cursor. / Local private voice typing.
#
#  Aufruf:   ./install.sh [--model tiny|base|small|medium|large-v3-turbo]
# ============================================================================
set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m    ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mFEHLER/ERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "Bitte als normaler Benutzer ausführen, NICHT als root/sudo."
command -v systemctl >/dev/null || die "Kein systemd – nicht unterstützt."

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="$HOME/.local/share/quassel"
LIB="$HOME/.local/lib/quassel"
BIN="$HOME/.local/bin"
VENV="$DATA/venv"
MODEL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) MODEL="${2:?}"; shift 2 ;;
        -h|--help) grep '^#' "$0" | head -8; exit 0 ;;
        *) die "Unbekannte Option: $1" ;;
    esac
done

# ----------------------------------------------------------------------------
say "1/8  Pakete installieren (sudo nötig)"
# ----------------------------------------------------------------------------
install_pkgs() {
    local mgr="$1"; shift
    for p in "$@"; do
        case "$mgr" in
            dnf)    sudo dnf install -y "$p" >/dev/null 2>&1 || warn "Paket '$p' nicht installierbar (evtl. anders benannt / schon da)" ;;
            apt)    sudo apt-get install -y "$p" >/dev/null 2>&1 || warn "Paket '$p' nicht installierbar" ;;
            pacman) sudo pacman -S --noconfirm --needed "$p" >/dev/null 2>&1 || warn "Paket '$p' nicht installierbar" ;;
            zypper) sudo zypper -n install "$p" >/dev/null 2>&1 || warn "Paket '$p' nicht installierbar" ;;
        esac
    done
}

if command -v dnf >/dev/null; then
    install_pkgs dnf git cmake gcc-c++ make curl ydotool wl-clipboard xclip \
        python3-gobject gtk4 gtk4-layer-shell libnotify pipewire-utils \
        python3-pip vulkan-loader-devel vulkan-headers glslc
elif command -v apt-get >/dev/null; then
    sudo apt-get update -qq || true
    # Hinweis: gtk4-layer-shell gibt es erst ab Ubuntu 24.10/Debian 13 —
    # auf älteren Versionen fällt die Pille auf ein normales Fenster zurück
    install_pkgs apt git cmake g++ make curl ydotool wl-clipboard xclip \
        python3-gi gir1.2-gtk-4.0 libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 \
        libnotify-bin pipewire-bin pulseaudio-utils python3-pip python3-venv \
        libvulkan-dev glslc glslang-tools
elif command -v pacman >/dev/null; then
    install_pkgs pacman git cmake gcc make curl ydotool wl-clipboard xclip \
        python-gobject gtk4 gtk4-layer-shell libnotify pipewire python-pip \
        vulkan-headers vulkan-icd-loader shaderc
elif command -v zypper >/dev/null; then
    # openSUSE versioniert Python-Pakete (python313-...) — Kandidaten durchprobieren
    install_pkgs zypper git cmake gcc-c++ make curl ydotool wl-clipboard xclip \
        libnotify-tools pipewire-tools \
        typelib-1_0-Gtk-4_0 libgtk4-layer-shell0 typelib-1_0-Gtk4LayerShell-1_0 \
        python313-gobject python312-gobject python311-gobject \
        python313-pip python312-pip python311-pip \
        vulkan-devel shaderc
else
    warn "Unbekannter Paketmanager. Bitte manuell installieren:"
    warn "git cmake g++ make curl ydotool wl-clipboard xclip python-gobject gtk4 gtk4-layer-shell libnotify pipewire python3-pip"
fi

MISSING=""
for c in git cmake curl ydotool ydotoold notify-send python3; do
    command -v "$c" >/dev/null || MISSING="$MISSING $c"
done
command -v pw-record >/dev/null || command -v parecord >/dev/null || MISSING="$MISSING pw-record/parecord"
python3 -c "import gi; gi.require_version('Gtk','4.0')" 2>/dev/null || MISSING="$MISSING python3-gobject+gtk4"
[[ -n "$MISSING" ]] && die "Fehlende Abhängigkeiten:$MISSING – bitte nachinstallieren, dann Skript erneut starten."
ok "Alle Pflicht-Abhängigkeiten vorhanden"

# ----------------------------------------------------------------------------
say "2/8  Berechtigungen (Tastatur lesen + virtuelle Tastatur)"
# ----------------------------------------------------------------------------
NEED_RELOGIN=0
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    sudo usermod -aG input "$USER"
    NEED_RELOGIN=1
    ok "Benutzer zur Gruppe 'input' hinzugefügt"
else
    ok "Benutzer ist bereits in Gruppe 'input'"
fi
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
    | sudo tee /etc/udev/rules.d/80-quassel-uinput.rules >/dev/null
echo uinput | sudo tee /etc/modules-load.d/quassel-uinput.conf >/dev/null
sudo modprobe uinput || true
sudo udevadm control --reload-rules && sudo udevadm trigger /dev/uinput 2>/dev/null || true
ok "udev-Regel für /dev/uinput installiert"

# ----------------------------------------------------------------------------
say "3/8  whisper.cpp bauen (einmalig; GPU wird automatisch erkannt)"
# ----------------------------------------------------------------------------
mkdir -p "$DATA" "$BIN" "$LIB"
if [[ ! -d "$DATA/whisper.cpp" ]]; then
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$DATA/whisper.cpp"
fi
GPU="CPU"
CMAKE_FLAGS=(-DCMAKE_BUILD_TYPE=Release)
if command -v nvcc >/dev/null; then
    CMAKE_FLAGS+=(-DGGML_CUDA=1); GPU="NVIDIA/CUDA"
elif command -v glslc >/dev/null && { pkg-config --exists vulkan 2>/dev/null || [[ -e /usr/include/vulkan/vulkan.h ]]; }; then
    CMAKE_FLAGS+=(-DGGML_VULKAN=1); GPU="Vulkan (AMD/Intel/NVIDIA)"
fi
build_whisper() {
    ( cd "$DATA/whisper.cpp" && rm -rf build &&
      cmake -B build "$@" >/dev/null &&
      cmake --build build -j"$(nproc)" --config Release >/dev/null )
}
if [[ ! -x "$DATA/whisper.cpp/build/bin/whisper-server" ]]; then
    if ! build_whisper "${CMAKE_FLAGS[@]}"; then
        warn "GPU-Build fehlgeschlagen – baue CPU-Version"
        GPU="CPU"
        build_whisper -DCMAKE_BUILD_TYPE=Release
    fi
fi
[[ -x "$DATA/whisper.cpp/build/bin/whisper-server" ]] || die "whisper.cpp-Build fehlgeschlagen"
ok "whisper.cpp gebaut ($GPU)"

# ----------------------------------------------------------------------------
say "4/8  Sprachmodell laden"
# ----------------------------------------------------------------------------
if [[ -z "$MODEL" ]]; then
    [[ "$GPU" == "CPU" ]] && MODEL="small" || MODEL="large-v3-turbo"
fi
MODELFILE="ggml-${MODEL}.bin"
mkdir -p "$DATA/models"
if [[ ! -s "$DATA/models/$MODELFILE" ]]; then
    echo "    Lade Modell '$MODEL' (75 MB – 1,6 GB)…"
    curl -L --progress-bar -o "$DATA/models/$MODELFILE" \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODELFILE"
fi
ok "Modell: $MODELFILE"

# ----------------------------------------------------------------------------
say "5/8  Qt-Oberfläche einrichten (PySide6 in eigener venv, ~150 MB einmalig)"
# ----------------------------------------------------------------------------
if [[ ! -x "$VENV/bin/python3" ]]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip PySide6 || die "PySide6-Installation fehlgeschlagen"
ok "PySide6 bereit"

# ----------------------------------------------------------------------------
say "6/8  Quassel installieren"
# ----------------------------------------------------------------------------
rm -rf "$LIB/quassel"
cp -r "$SRC/quassel" "$LIB/quassel"
install -m 755 "$SRC/bin/quasseld" "$SRC/bin/quassel-type" "$SRC/bin/quassel-pill" \
               "$SRC/bin/quassel-ctl" "$BIN/"
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/share/applications" \
         "$HOME/.config/quassel"
install -m 644 "$SRC"/systemd/*.service "$HOME/.config/systemd/user/"
if [[ ! -s "$HOME/.config/quassel/server.env" ]]; then
    cat > "$HOME/.config/quassel/server.env" <<EOF
SERVER_BIN=$DATA/whisper.cpp/build/bin/whisper-server
MODEL_PATH=$DATA/models/$MODELFILE
EOF
fi
sed "s|@HOME@|$HOME|g" "$SRC/desktop/quassel.desktop.in" \
    > "$HOME/.local/share/applications/quassel.desktop"
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
install -m 644 "$SRC/assets/quassel.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/quassel.svg"
for sz in 48 64 128 256; do
    mkdir -p "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps"
    install -m 644 "$SRC/assets/icons/quassel-${sz}.png"         "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps/quassel.png"
done
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
systemctl --user daemon-reload
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 2>/dev/null || true
ok "Quassel installiert (KEIN Autostart — Start über die Quassel-App)"

# ----------------------------------------------------------------------------
say "7/8  Alte »Diktat«-Version entfernen (falls vorhanden)"
# ----------------------------------------------------------------------------
if [[ -f "$HOME/.config/systemd/user/dictate-daemon.service" || -f "$BIN/dictate-daemon" ]]; then
    systemctl --user stop dictate-daemon whisper-server diktat-ydotoold diktat-indicator 2>/dev/null || true
    systemctl --user disable dictate-daemon whisper-server 2>/dev/null || true
    rm -f "$BIN"/dictate-daemon "$BIN"/dictate-gui "$BIN"/dictate-ctl "$BIN"/dictate \
          "$HOME/.config/systemd/user"/dictate-daemon.service \
          "$HOME/.config/systemd/user"/diktat-*.service \
          "$HOME/.local/share/applications"/dictate.desktop \
          "$HOME/.local/share/applications"/diktat.desktop
    systemctl --user daemon-reload
    ok "Alte Diktat-Installation entfernt"
else
    ok "Keine Alt-Installation gefunden"
fi

# ----------------------------------------------------------------------------
say "8/8  Fertig!"
# ----------------------------------------------------------------------------
echo
if [[ "$NEED_RELOGIN" -eq 1 ]]; then
    warn "WICHTIG: Einmal ab- und wieder anmelden (oder neu starten),"
    warn "damit die Gruppen-Berechtigung wirksam wird!"
    echo
fi
cat <<'ANLEITUNG'
    So geht's / How to:
      1. App »Quassel« im Startmenü öffnen / open "Quassel" from your launcher
      2. Einschalten / turn it ON
      3. In ein Textfeld klicken / click into any text field:
         • Strg+Meta HALTEN -> sprechen -> loslassen -> Text erscheint
           hold Ctrl+Meta -> speak -> release -> text appears
         • Strg+Meta 2× TIPPEN -> freihändig -> 1× drücken -> Text erscheint
           double-tap -> speak hands-free -> press once -> text appears
ANLEITUNG
