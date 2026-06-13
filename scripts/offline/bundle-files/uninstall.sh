#!/usr/bin/env bash
# Quassel offline bundle — Deinstallation (entfernt Units/Launcher/Eintrag).
# Das entpackte Paket selbst kannst du danach einfach löschen.
set -uo pipefail
BIN="$HOME/.local/bin"
UNITS="$HOME/.config/systemd/user"
say(){ printf '\033[1;36m==> %s\033[0m\n' "$*"; }

say "Quassel stoppen + Autostart aus"
systemctl --user disable --now quasseld quassel-server quassel-pill quassel-ydotoold 2>/dev/null || true

say "Units + Launcher + App-Eintrag entfernen"
rm -f "$UNITS"/quasseld.service "$UNITS"/quassel-server.service \
      "$UNITS"/quassel-pill.service "$UNITS"/quassel-ydotoold.service
rm -f "$BIN"/quasseld "$BIN"/quassel-pill "$BIN"/quassel-type "$BIN"/quassel-ctl
rm -f "$HOME/.local/share/applications/quassel.desktop"
systemctl --user daemon-reload 2>/dev/null || true

printf '\033[1;32mFertig.\033[0m Konfiguration/Verlauf in ~/.config/quassel und\n'
printf '~/.local/share/quassel bleiben erhalten (bei Bedarf manuell löschen).\n'
printf 'Die uinput-udev-Regel: sudo rm /etc/udev/rules.d/80-quassel-uinput.rules\n'
