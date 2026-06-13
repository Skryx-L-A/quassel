#!/usr/bin/env bash
# ============================================================================
#  Quassel — kleiner Online-Installer (Linux) / small online installer
#
#  Eine winzige Datei: herunterladen und ausführen. Sie lädt Quassel und richtet
#  es ein. Standard ist SCHLANK: vorgebaute Engine + ein zur Hardware passendes
#  Modell (kein Kompilieren). Die großen Offline-Komplettpakete gibt es separat
#  auf der Release-Seite.
#
#  Aufruf:
#     bash quassel-install.sh                 # schlank (Standard)
#     bash quassel-install.sh --all           # alle 5 Modelle laden
#     bash quassel-install.sh --model medium  # bestimmtes Modell
#     bash quassel-install.sh --build-from-source   # Engine selbst kompilieren
#
#  (Doppelklick: in vielen Linux-Dateimanagern muss eine .sh erst ausführbar
#   gemacht werden — `chmod +x quassel-install.sh` — oder im Terminal starten.
#   Für echtes Doppelklicken gibt es die .run-Variante.)
# ============================================================================
set -euo pipefail
REPO="https://github.com/Skryx-L-A/quassel"

PREBUILT=1
EXTRA=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-from-source) PREBUILT=0; shift ;;
        --all)   EXTRA+=(--all); shift ;;
        --model) EXTRA+=(--model "${2:?--model braucht einen Wert}"); shift 2 ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done
INSTALL_ARGS=()
[[ "$PREBUILT" -eq 1 ]] && INSTALL_ARGS+=(--prebuilt)
[[ ${#EXTRA[@]} -gt 0 ]] && INSTALL_ARGS+=("${EXTRA[@]}")

command -v curl >/dev/null || { echo "curl wird benötigt."; exit 1; }
command -v tar  >/dev/null || { echo "tar wird benötigt.";  exit 1; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
printf '\n\033[1;36m==> Quassel herunterladen…\033[0m\n'
# Neuesten Release-Tag ermitteln (Fallback: main)
TAG="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REPO/releases/latest" 2>/dev/null | sed 's#.*/##')"
SRC_URL="$REPO/archive/refs/tags/$TAG.tar.gz"
[[ -n "$TAG" ]] && curl -fsSL "$SRC_URL" -o "$TMP/src.tgz" 2>/dev/null \
    || curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" -o "$TMP/src.tgz" \
    || { echo "Download fehlgeschlagen."; exit 1; }

tar -xzf "$TMP/src.tgz" -C "$TMP"
DIR="$(find "$TMP" -maxdepth 1 -type d -name 'quassel-*' | head -1)"
[[ -d "$DIR" && -f "$DIR/install.sh" ]] || { echo "Quelle unvollständig."; exit 1; }

printf '\033[1;36m==> Installation starten…\033[0m\n'
cd "$DIR"
exec bash ./install.sh "${INSTALL_ARGS[@]}"
