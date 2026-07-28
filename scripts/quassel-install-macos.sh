#!/usr/bin/env bash
# ============================================================================
#  Quassel — Ein-Zeilen-Installer (macOS, Apple Silicon)
#
#  Lädt das neueste Release-DMG, prüft (falls vorhanden) die SHA256-Summe,
#  kopiert Quassel.app nach /Applications und entfernt das Quarantäne-Flag
#  (kein Apple-Developer-Account -> kein Notarization-Ticket, ohne das würde
#  Gatekeeper die App sonst blockieren).
#
#  Aufruf:
#     curl -fsSL https://github.com/Skryx-L-A/quassel/releases/latest/download/quassel-install-macos.sh | bash
#
#  Auch als heruntergeladene Datei lauffähig:
#     bash quassel-install-macos.sh
#
#  Für Tests/Entwicklung kann die Quelle überschrieben werden (kein Download
#  vom Internet nötig):
#     QUASSEL_DMG_URL="file:///pfad/zu/Quassel-macOS-arm64.dmg" bash quassel-install-macos.sh
# ============================================================================
set -euo pipefail
REPO="https://github.com/Skryx-L-A/quassel"

command -v curl >/dev/null || { echo "curl wird benötigt."; exit 1; }
command -v hdiutil >/dev/null || { echo "hdiutil wird benötigt (nur auf macOS vorhanden)."; exit 1; }

# Das Bundle enthält nur arm64-Code (whisper.cpp mit Metal) — auf einem
# Intel-Mac ließe es sich installieren, aber nicht starten.
if [[ "$(uname -m)" != "arm64" ]]; then
    echo "FEHLER: Quassel für macOS gibt es nur für Apple Silicon (arm64), erkannt: $(uname -m)."
    exit 1
fi

TMP="$(mktemp -d)"
MOUNT="$TMP/mnt"
DETACHED=0
cleanup() {
    if [[ "$DETACHED" -eq 0 ]] && mount | grep -q "$MOUNT"; then
        hdiutil detach "$MOUNT" -quiet 2>/dev/null || true
    fi
    rm -rf "$TMP"
}
trap cleanup EXIT

# Quelle: Umgebungsvariable (Test/Entwicklung) oder neuestes GitHub-Release.
DMG_URL="${QUASSEL_DMG_URL:-}"
if [[ -z "$DMG_URL" ]]; then
    printf '\n\033[1;36m==> Neuestes Release ermitteln…\033[0m\n'
    TAG="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REPO/releases/latest" 2>/dev/null | sed 's#.*/##')"
    [[ -n "$TAG" ]] || { echo "FEHLER: Konnte neuestes Release nicht ermitteln."; exit 1; }
    DMG_URL="$REPO/releases/download/$TAG/Quassel-macOS-arm64.dmg"
fi
SHA_URL="${QUASSEL_SHA_URL:-$DMG_URL.sha256}"

# file:// (Trockenlauf) per cp, sonst per curl — curl blockiert das
# file-Protokoll standardmäßig aus Sicherheitsgründen.
fetch() {
    local url="$1" dest="$2"
    if [[ "$url" == file://* ]]; then
        cp "${url#file://}" "$dest"
    else
        curl -fsSL "$url" -o "$dest"
    fi
}

printf '\033[1;36m==> Quassel herunterladen…\033[0m\n'
fetch "$DMG_URL" "$TMP/Quassel.dmg" || { echo "FEHLER: Download von $DMG_URL fehlgeschlagen."; exit 1; }

if fetch "$SHA_URL" "$TMP/Quassel.dmg.sha256" 2>/dev/null; then
    EXPECTED="$(cut -d' ' -f1 < "$TMP/Quassel.dmg.sha256" | tr -d '[:space:]')"
    ACTUAL="$(shasum -a 256 "$TMP/Quassel.dmg" | cut -d' ' -f1)"
    if [[ "$EXPECTED" != "$ACTUAL" ]]; then
        echo "FEHLER: SHA256 stimmt nicht überein."
        echo "  erwartet: $EXPECTED"
        echo "  erhalten: $ACTUAL"
        exit 1
    fi
    echo "SHA256 geprüft: OK"
else
    echo "Hinweis: keine Prüfsummendatei gefunden — SHA256-Prüfung übersprungen."
fi

printf '\033[1;36m==> Image mounten…\033[0m\n'
mkdir -p "$MOUNT"
hdiutil attach "$TMP/Quassel.dmg" -mountpoint "$MOUNT" -nobrowse -readonly -quiet

[[ -d "$MOUNT/Quassel.app" ]] || { echo "FEHLER: Quassel.app nicht im Image gefunden."; exit 1; }

if [[ -d /Applications/Quassel.app ]]; then
    # Zeitstempel im Namen: eine zweite Installation am selben Tag darf die
    # erste Sicherung nicht überschreiben oder in sie hineinwandern.
    SNAP="$HOME/.local/trash-snapshots/$(date +%Y-%m-%d-%H%M%S)-quassel-app"
    mkdir -p "$(dirname "$SNAP")"
    echo "Vorhandene Version sichern nach $SNAP"
    mv /Applications/Quassel.app "$SNAP"
fi

printf '\033[1;36m==> Installieren…\033[0m\n'
# ditto statt cp -R: erhält erweiterte Attribute und die Signatur des Bundles.
ditto "$MOUNT/Quassel.app" /Applications/Quassel.app

hdiutil detach "$MOUNT" -quiet
DETACHED=1

# Selbstsigniert, nicht notarisiert — ohne das entfernte Quarantäne-Flag
# würde Gatekeeper den ersten Start blockieren.
xattr -dr com.apple.quarantine /Applications/Quassel.app 2>/dev/null || true

cat <<'EOF'

==> Installiert: /Applications/Quassel.app

Beim ersten Start fragt macOS pro Berechtigung einmalig nach:
  - Mikrofon              (Aufnahme)
  - Bedienungshilfen      (Hotkey per CGEventTap)
  - Eingabeüberwachung    (Hotkey-Erkennung, Einfügen)

Systemeinstellungen > Datenschutz & Sicherheit, jeweils Quassel.app
freigeben, danach die App einmal neu starten. Erster Start:

  open -a Quassel

EOF
