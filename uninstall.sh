#!/usr/bin/env bash
# Diktat entfernen.  ./uninstall.sh [--purge]   (--purge löscht auch whisper.cpp + Modelle)
set -u

systemctl --user stop dictate-daemon whisper-server diktat-ydotoold 2>/dev/null
systemctl --user disable dictate-daemon whisper-server diktat-ydotoold 2>/dev/null

rm -f "$HOME/.local/bin/dictate-daemon" "$HOME/.local/bin/dictate-gui" "$HOME/.local/bin/dictate-ctl"
rm -f "$HOME/.config/systemd/user/dictate-daemon.service" \
      "$HOME/.config/systemd/user/whisper-server.service" \
      "$HOME/.config/systemd/user/diktat-ydotoold.service"
rm -f "$HOME/.local/share/applications/diktat.desktop"
systemctl --user daemon-reload

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$HOME/.local/share/diktat"
    echo "whisper.cpp + Modelle gelöscht."
fi

echo "Diktat entfernt. (udev-Regel /etc/udev/rules.d/80-diktat-uinput.rules"
echo "und Gruppen-Mitgliedschaft 'input' bleiben — bei Bedarf mit sudo entfernen.)"
