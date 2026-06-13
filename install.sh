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
PREBUILT=0          # --prebuilt: vorgebaute Engine laden statt kompilieren
ALL_MODELS=0        # --all: alle Modelle statt nur des passenden
RELEASE="https://github.com/Skryx-L-A/quassel/releases/latest/download"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) MODEL="${2:?}"; shift 2 ;;
        --prebuilt) PREBUILT=1; shift ;;
        --all) ALL_MODELS=1; shift ;;
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
say "3/8  Spracherkennungs-Engine (vorgebaut laden ODER selbst kompilieren)"
# ----------------------------------------------------------------------------
mkdir -p "$DATA" "$BIN" "$LIB"
HAS_GPU=0
if [[ "$PREBUILT" -eq 1 ]]; then
    # Vorgebaute portable Engine aus dem Release laden — kein git/Compiler nötig.
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        EVAR="cuda"; HAS_GPU=1
    else
        EVAR="cpu"
    fi
    EDIR="$DATA/engine/$EVAR"
    if [[ ! -x "$EDIR/whisper-server" ]]; then
        mkdir -p "$EDIR"
        echo "    Lade vorgebaute Engine ($EVAR)…"
        curl -L --fail --progress-bar -o "$DATA/engine-$EVAR.tar.gz" \
            "$RELEASE/quassel-engine-linux-$EVAR-x86_64.tar.gz" \
            || die "Engine-Download fehlgeschlagen ($EVAR)"
        tar -xzf "$DATA/engine-$EVAR.tar.gz" -C "$DATA/engine"
        rm -f "$DATA/engine-$EVAR.tar.gz"
    fi
    # Wrapper: gebündelte Libs prozess-lokal (System bleibt unangetastet);
    # der NVIDIA-Treiber kommt zur Laufzeit immer vom Zielsystem.
    cat > "$EDIR/run-server.sh" <<'WRAP'
#!/usr/bin/env bash
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$here:${LD_LIBRARY_PATH:-}"
exec "$here/whisper-server" "$@"
WRAP
    chmod +x "$EDIR/run-server.sh"
    SERVER_BIN_PATH="$EDIR/run-server.sh"
    ok "Engine (vorgebaut): $EVAR"
else
    if [[ ! -d "$DATA/whisper.cpp" ]]; then
        git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$DATA/whisper.cpp"
    fi
    GPU="CPU"; CMAKE_FLAGS=(-DCMAKE_BUILD_TYPE=Release)
    if command -v nvcc >/dev/null; then
        CMAKE_FLAGS+=(-DGGML_CUDA=1); GPU="NVIDIA/CUDA"; HAS_GPU=1
    elif command -v glslc >/dev/null && { pkg-config --exists vulkan 2>/dev/null || [[ -e /usr/include/vulkan/vulkan.h ]]; }; then
        CMAKE_FLAGS+=(-DGGML_VULKAN=1); GPU="Vulkan (AMD/Intel/NVIDIA)"; HAS_GPU=1
    fi
    build_whisper() {
        ( cd "$DATA/whisper.cpp" && rm -rf build &&
          cmake -B build "$@" >/dev/null &&
          cmake --build build -j"$(nproc)" --config Release >/dev/null )
    }
    if [[ ! -x "$DATA/whisper.cpp/build/bin/whisper-server" ]]; then
        if ! build_whisper "${CMAKE_FLAGS[@]}"; then
            warn "GPU-Build fehlgeschlagen – baue CPU-Version"
            GPU="CPU"; HAS_GPU=0
            build_whisper -DCMAKE_BUILD_TYPE=Release
        fi
    fi
    [[ -x "$DATA/whisper.cpp/build/bin/whisper-server" ]] || die "whisper.cpp-Build fehlgeschlagen"
    SERVER_BIN_PATH="$DATA/whisper.cpp/build/bin/whisper-server"
    ok "whisper.cpp gebaut ($GPU)"
fi

# ----------------------------------------------------------------------------
say "4/8  Sprachmodell(e) laden"
# ----------------------------------------------------------------------------
mkdir -p "$DATA/models"
HF="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
dl_model() {
    local m="$1" f="ggml-$1.bin"
    [[ -s "$DATA/models/$f" ]] && return 0
    echo "    Lade Modell '$m' (75 MB – 1,6 GB)…"
    curl -L --fail --progress-bar -o "$DATA/models/$f" "$HF/$f" \
        || die "Modell-Download fehlgeschlagen: $m"
}
[[ -z "$MODEL" ]] && { [[ "$HAS_GPU" -eq 1 ]] && MODEL="large-v3-turbo" || MODEL="small"; }
if [[ "$ALL_MODELS" -eq 1 ]]; then
    for m in tiny base small medium large-v3-turbo; do dl_model "$m"; done
else
    dl_model "$MODEL"
fi
MODELFILE="ggml-${MODEL}.bin"
ok "Modell: $MODELFILE  (Standard für diese Hardware)"

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
SERVER_BIN=$SERVER_BIN_PATH
MODEL_PATH=$DATA/models/$MODELFILE
EOF
fi
sed "s|@HOME@|$HOME|g" "$SRC/desktop/quassel.desktop.in" \
    > "$HOME/.local/share/applications/quassel.desktop"
# Unter eindeutigem Namen installieren: der Icon-Theme-Name "quassel" gehört
# dem Quassel-IRC-Client (Papirus/Breeze/Oxygen liefern ihn) und würde unser
# Symbol in Taskleiste/Starter überdecken. "quassel-voice" ist kollisionsfrei.
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
install -m 644 "$SRC/assets/quassel.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/quassel-voice.svg"
for sz in 48 64 128 256; do
    mkdir -p "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps"
    install -m 644 "$SRC/assets/icons/quassel-${sz}.png"         "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps/quassel-voice.png"
done
# evtl. alt installiertes (kollidierendes) Symbol entfernen
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/quassel.svg" \
      "$HOME"/.local/share/icons/hicolor/*/apps/quassel.png 2>/dev/null || true
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
