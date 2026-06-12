#!/usr/bin/env bash
# VoxType entfernen.  ./uninstall.sh [--purge]  (--purge löscht auch whisper.cpp,
# Modelle, venv, Verlauf und Einstellungen)
set -u

systemctl --user stop voxtyped voxtype-server voxtype-pill voxtype-ydotoold 2>/dev/null
systemctl --user disable voxtyped voxtype-server 2>/dev/null

rm -f "$HOME"/.local/bin/{voxtyped,voxtype,voxtype-pill,voxtype-ctl}
rm -f "$HOME"/.config/systemd/user/voxtype*.service \
      "$HOME"/.config/systemd/user/voxtyped.service
rm -f "$HOME/.local/share/applications/voxtype.desktop"
rm -rf "$HOME/.local/lib/voxtype"
systemctl --user daemon-reload

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$HOME/.local/share/voxtype" "$HOME/.config/voxtype"
    echo "whisper.cpp, Modelle, venv, Verlauf und Einstellungen gelöscht."
fi

echo "VoxType entfernt. (udev-Regel /etc/udev/rules.d/80-voxtype-uinput.rules"
echo "und Gruppen-Mitgliedschaft 'input' bleiben — bei Bedarf mit sudo entfernen.)"
