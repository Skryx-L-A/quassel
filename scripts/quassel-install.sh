#!/usr/bin/env bash
# ============================================================================
#  Quassel — kleiner Online-Installer (Linux) / small online installer
#
#  Eine winzige Datei: herunterladen und ausführen. Sie lädt Quassel und richtet
#  es ein: ein zur Hardware passendes Modell und die Spracherkennungs-Engine.
#  Hat das Release vorgebaute Engine-Pakete, werden sie geladen; sonst baut der
#  Installer whisper.cpp (fester Stand) selbst – das dauert einige Minuten und
#  braucht Compiler, die er mitinstalliert. Die großen Offline-Komplettpakete
#  gibt es nur für ältere Releases (v2.2.0).
#
#  Aufruf:
#     bash quassel-install.sh                 # schlank (Standard)
#     bash quassel-install.sh --all           # alle 5 Modelle laden
#     bash quassel-install.sh --model medium  # bestimmtes Modell
#     bash quassel-install.sh --build-from-source   # Engine immer selbst kompilieren
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
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
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
# Neuesten Release-Tag ermitteln. Eine Installation darf nie still auf
# unveröffentlichten Branch-Code wechseln.
TAG="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REPO/releases/latest" 2>/dev/null | sed 's#.*/##')"
[[ "$TAG" =~ ^v?[0-9]+(\.[0-9]+){1,3}([.-][A-Za-z0-9]+)?$ ]] \
    || { echo "Release-Tag konnte nicht sicher ermittelt werden."; exit 1; }
SRC_URL="$REPO/archive/refs/tags/$TAG.tar.gz"
curl -fsSL "$SRC_URL" -o "$TMP/src.tgz" \
    || { echo "Download des Release $TAG fehlgeschlagen."; exit 1; }

tar -xzf "$TMP/src.tgz" -C "$TMP"
DIR="$(find "$TMP" -maxdepth 1 -type d -name 'quassel-*' | head -1)"
[[ -d "$DIR" && -f "$DIR/install.sh" ]] || { echo "Quelle unvollständig."; exit 1; }

printf '\033[1;36m==> Installation starten…\033[0m\n'
cd "$DIR"
# Der Installer kennt den Tag nicht aus dem Archiv; so fragt er die Pakete
# genau dieses Release ab und nicht "latest".
export QUASSEL_RELEASE_TAG="$TAG"
exec bash ./install.sh ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}
