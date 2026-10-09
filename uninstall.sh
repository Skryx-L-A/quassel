#!/usr/bin/env bash
# Quassel entfernen. ./uninstall.sh [--purge]
# --purge löscht zusätzlich Engine, Modelle, venv, Verlauf und Einstellungen.
set -u

PURGE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=1 ;;
        -h|--help)
            echo "Aufruf: ./uninstall.sh [--purge]"
            exit 0
            ;;
        *)
            echo "FEHLER/ERROR: Unbekannte Option: $1" >&2
            exit 2
            ;;
    esac
    shift
done

BIN="$HOME/.local/bin"
UNITS="$HOME/.config/systemd/user"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"

systemctl --user stop quasseld quassel-server quassel-pill quassel-ydotoold \
    2>/dev/null || true
systemctl --user disable quasseld quassel-server quassel-pill quassel-ydotoold \
    2>/dev/null || true

rm -f "$BIN"/quasseld "$BIN"/quassel-type "$BIN"/quassel-pill \
      "$BIN"/quassel-ctl "$BIN"/quassel-python "$BIN"/quassel-mac \
      "$BIN"/quassel-type-cli
rm -f "$UNITS"/quassel*.service
rm -f "$APPS/quassel.desktop"
rm -f "$ICONS/scalable/apps/quassel-voice.svg"
for size in 48 64 128 256; do
    rm -f "$ICONS/${size}x${size}/apps/quassel-voice.png"
done
rm -rf "$HOME/.local/lib/quassel"
systemctl --user daemon-reload 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null \
    && gtk-update-icon-cache -q "$ICONS" 2>/dev/null || true
command -v update-desktop-database >/dev/null \
    && update-desktop-database "$APPS" 2>/dev/null || true

if [[ "$PURGE" -eq 1 ]]; then
    rm -rf "$HOME/.local/share/quassel" "$HOME/.config/quassel"
    echo "Engine, Modelle, venv, Verlauf und Einstellungen gelöscht."
fi

echo
echo "Quassel-Benutzerdateien wurden entfernt."
echo "Die folgenden Systemrechte wurden von Quassel angelegt:"
echo "  - /etc/udev/rules.d/80-quassel-uinput.rules"
echo "  - /etc/modules-load.d/quassel-uinput.conf"
echo "  - Mitgliedschaft von $USER in der Gruppe input"
echo "Die Gruppe input kann auch von anderen Programmen benötigt werden."
read -r -p "Diese Quassel-Systemrechte jetzt ebenfalls entfernen? [y/N] " answer || answer=""
case "$answer" in
    y|Y|yes|YES|Yes)
        sudo rm -f /etc/udev/rules.d/80-quassel-uinput.rules \
            /etc/modules-load.d/quassel-uinput.conf
        sudo udevadm control --reload-rules 2>/dev/null || true
        if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
            if command -v gpasswd >/dev/null; then
                sudo gpasswd -d "$USER" input \
                    || echo "WARNUNG: Gruppen-Mitgliedschaft nicht entfernt." >&2
            else
                echo "WARNUNG: gpasswd fehlt; input-Mitgliedschaft bleibt bestehen." >&2
            fi
        fi
        echo "Quassel-Systemrechte entfernt. Bitte einmal ab- und wieder anmelden."
        ;;
    *)
        echo "Quassel-Systemrechte bleiben unverändert."
        ;;
esac
