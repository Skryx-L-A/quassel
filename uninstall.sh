#!/usr/bin/env bash
# Quassel entfernen.  ./uninstall.sh [--purge]  (--purge löscht auch whisper.cpp,
# Modelle, venv, Verlauf und Einstellungen)
set -u

systemctl --user stop quasseld quassel-server quassel-pill quassel-ydotoold 2>/dev/null
systemctl --user disable quasseld quassel-server 2>/dev/null

rm -f "$HOME"/.local/bin/{quasseld,quassel,quassel-pill,quassel-ctl}
rm -f "$HOME"/.config/systemd/user/quassel*.service \
      "$HOME"/.config/systemd/user/quasseld.service
rm -f "$HOME/.local/share/applications/quassel.desktop"
rm -rf "$HOME/.local/lib/quassel"
systemctl --user daemon-reload

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$HOME/.local/share/quassel" "$HOME/.config/quassel"
    echo "whisper.cpp, Modelle, venv, Verlauf und Einstellungen gelöscht."
fi

echo "Quassel entfernt. (udev-Regel /etc/udev/rules.d/80-quassel-uinput.rules"
echo "und Gruppen-Mitgliedschaft 'input' bleiben — bei Bedarf mit sudo entfernen.)"
