#!/usr/bin/env bash
# ============================================================================
#  Diktat – Installation
#  Systemweite Spracheingabe (Speech-to-Text) für Linux:
#  Strg+Meta halten -> sprechen -> loslassen -> Text erscheint am Cursor.
#
#  Aufruf:   ./install.sh [--model tiny|base|small|medium|large-v3-turbo]
#  Beispiel: ./install.sh --model small
# ============================================================================
set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m    ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mFEHLER: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "Bitte als normaler Benutzer ausführen, NICHT als root/sudo. Das Skript fragt selbst nach sudo, wo nötig."
command -v systemctl >/dev/null || die "Dieses System nutzt kein systemd – wird nicht unterstützt."

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="$HOME/.local/share/diktat"
BIN="$HOME/.local/bin"
MODEL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) MODEL="${2:?}"; shift 2 ;;
        -h|--help) grep '^#' "$0" | head -9; exit 0 ;;
        *) die "Unbekannte Option: $1" ;;
    esac
done

# ----------------------------------------------------------------------------
say "1/7  Pakete installieren (sudo nötig)"
# ----------------------------------------------------------------------------
install_pkgs() {
    local mgr="$1"; shift
    for p in "$@"; do
        case "$mgr" in
            dnf)    sudo dnf install -y "$p" >/dev/null 2>&1 || warn "Paket '$p' nicht installierbar (evtl. anders benannt / schon da)" ;;
            apt)    sudo apt-get install -y "$p" >/dev/null 2>&1 || warn "Paket '$p' nicht installierbar (evtl. anders benannt / schon da)" ;;
            pacman) sudo pacman -S --noconfirm --needed "$p" >/dev/null 2>&1 || warn "Paket '$p' nicht installierbar" ;;
            zypper) sudo zypper -n install "$p" >/dev/null 2>&1 || warn "Paket '$p' nicht installierbar" ;;
        esac
    done
}

if command -v dnf >/dev/null; then
    install_pkgs dnf git cmake gcc-c++ make curl ydotool wl-clipboard xclip \
        python3-gobject gtk4 libnotify pipewire-utils
elif command -v apt-get >/dev/null; then
    sudo apt-get update -qq || true
    install_pkgs apt git cmake g++ make curl ydotool wl-clipboard xclip \
        python3-gi gir1.2-gtk-4.0 libnotify-bin pipewire-bin pulseaudio-utils
elif command -v pacman >/dev/null; then
    install_pkgs pacman git cmake gcc make curl ydotool wl-clipboard xclip \
        python-gobject gtk4 libnotify pipewire
elif command -v zypper >/dev/null; then
    install_pkgs zypper git cmake gcc-c++ make curl ydotool wl-clipboard xclip \
        python3-gobject libnotify-tools pipewire-tools
else
    warn "Unbekannter Paketmanager. Bitte manuell installieren:"
    warn "git cmake g++ make curl ydotool wl-clipboard xclip python-gobject gtk4 libnotify pipewire(-utils)"
fi

MISSING=""
for c in git cmake curl ydotool ydotoold notify-send; do
    command -v "$c" >/dev/null || MISSING="$MISSING $c"
done
command -v pw-record >/dev/null || command -v parecord >/dev/null || MISSING="$MISSING pw-record/parecord"
python3 -c "import gi; gi.require_version('Gtk','4.0')" 2>/dev/null || MISSING="$MISSING python3-gobject+gtk4"
[[ -n "$MISSING" ]] && die "Fehlende Abhängigkeiten:$MISSING – bitte nachinstallieren und Skript erneut starten."
ok "Alle Abhängigkeiten vorhanden"

# ----------------------------------------------------------------------------
say "2/7  Berechtigungen einrichten (Tastatur lesen + virtuelle Tastatur)"
# ----------------------------------------------------------------------------
NEED_RELOGIN=0
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    sudo usermod -aG input "$USER"
    NEED_RELOGIN=1
    ok "Benutzer zur Gruppe 'input' hinzugefügt"
else
    ok "Benutzer ist bereits in Gruppe 'input'"
fi
# /dev/uinput für Gruppe 'input' freigeben (für die virtuelle Tastatur ydotoold)
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
    | sudo tee /etc/udev/rules.d/80-diktat-uinput.rules >/dev/null
echo uinput | sudo tee /etc/modules-load.d/diktat-uinput.conf >/dev/null
sudo modprobe uinput || true
sudo udevadm control --reload-rules && sudo udevadm trigger /dev/uinput 2>/dev/null || true
ok "udev-Regel für /dev/uinput installiert"

# ----------------------------------------------------------------------------
say "3/7  whisper.cpp herunterladen und bauen (einmalig, dauert ein paar Minuten)"
# ----------------------------------------------------------------------------
mkdir -p "$DATA" "$BIN"
if [[ ! -d "$DATA/whisper.cpp" ]]; then
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$DATA/whisper.cpp"
fi
CMAKE_FLAGS=(-DCMAKE_BUILD_TYPE=Release)
GPU="CPU"
if command -v nvcc >/dev/null; then
    CMAKE_FLAGS+=(-DGGML_CUDA=1); GPU="NVIDIA/CUDA"
fi
if [[ ! -x "$DATA/whisper.cpp/build/bin/whisper-server" ]]; then
    ( cd "$DATA/whisper.cpp" &&
      cmake -B build "${CMAKE_FLAGS[@]}" >/dev/null &&
      cmake --build build -j"$(nproc)" --config Release >/dev/null )
fi
[[ -x "$DATA/whisper.cpp/build/bin/whisper-server" ]] || die "whisper.cpp-Build fehlgeschlagen"
ok "whisper.cpp gebaut ($GPU)"

# ----------------------------------------------------------------------------
say "4/7  Sprachmodell herunterladen"
# ----------------------------------------------------------------------------
# Ohne --model: gute Wahl automatisch — GPU: bestes Modell, CPU: kleines (schneller)
if [[ -z "$MODEL" ]]; then
    [[ "$GPU" == "CPU" ]] && MODEL="small" || MODEL="large-v3-turbo"
fi
MODELFILE="ggml-${MODEL}.bin"
mkdir -p "$DATA/models"
if [[ ! -s "$DATA/models/$MODELFILE" ]]; then
    echo "    Lade Modell '$MODEL' (das kann je nach Modell 75 MB bis 1,6 GB sein)…"
    curl -L --progress-bar -o "$DATA/models/$MODELFILE" \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODELFILE"
fi
ok "Modell: $MODELFILE"

# ----------------------------------------------------------------------------
say "5/7  Programme installieren"
# ----------------------------------------------------------------------------
install -m 755 "$SRC/bin/dictate-daemon" "$SRC/bin/dictate-gui" "$SRC/bin/dictate-ctl" "$BIN/"
ok "Skripte in $BIN installiert"

# ----------------------------------------------------------------------------
say "6/7  Dienste einrichten (KEIN Autostart — Start über die Diktat-App)"
# ----------------------------------------------------------------------------
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/share/applications"
install -m 644 "$SRC/systemd/dictate-daemon.service" \
               "$SRC/systemd/diktat-ydotoold.service" "$HOME/.config/systemd/user/"
sed "s|@MODEL@|$MODELFILE|" "$SRC/systemd/whisper-server.service.in" \
    > "$HOME/.config/systemd/user/whisper-server.service"
sed "s|@HOME@|$HOME|g" "$SRC/desktop/diktat.desktop.in" \
    > "$HOME/.local/share/applications/diktat.desktop"
systemctl --user daemon-reload
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 2>/dev/null || true
ok "Dienste + Startmenü-Eintrag »Diktat« installiert"

# ----------------------------------------------------------------------------
say "7/7  Fertig!"
# ----------------------------------------------------------------------------
echo
if [[ "$NEED_RELOGIN" -eq 1 ]]; then
    warn "WICHTIG: Einmal ab- und wieder anmelden (oder neu starten),"
    warn "damit die Gruppen-Berechtigung wirksam wird!"
    echo
fi
cat <<'ANLEITUNG'
    So geht's:
      1. App »Diktat« im Startmenü suchen und öffnen
      2. Schalter auf AN
      3. In ein Textfeld klicken, dann:
         • Strg+Meta(Windows-Taste) HALTEN  -> sprechen -> loslassen -> Text erscheint
         • Strg+Meta 2× TIPPEN -> freihändig sprechen -> 1× drücken -> Text erscheint

    Die erste Transkription lädt das Modell (ein paar Sekunden), danach geht es schnell.
ANLEITUNG
