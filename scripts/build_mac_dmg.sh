#!/usr/bin/env bash
# Aus dist/Quassel.app ein auslieferbares DMG bauen (hdiutil, komprimiert).
# Aufruf aus dem Repo-Root:  scripts/build_mac_dmg.sh
# Voraussetzung: dist/Quassel.app existiert bereits (scripts/build_mac_app.sh).
# Ergebnis: dist/Quassel-macOS-arm64.dmg (Volume "Quassel", /Applications-Symlink
# daneben — Drag-and-drop-Installation im gewohnten macOS-Layout).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$REPO/dist/Quassel.app"
DMG="$REPO/dist/Quassel-macOS-arm64.dmg"
VOLNAME="Quassel"

[ -d "$APP" ] || { echo "FEHLER: $APP fehlt (erst scripts/build_mac_app.sh laufen lassen)"; exit 1; }

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

# Staging-Ordner statt direkt dist/, damit im DMG nur App + Symlink liegen.
# ditto statt cp -R: übernimmt erweiterte Attribute und ACLs, sonst kann die
# Signatur des Bundles im Image beschädigt ankommen.
ditto "$APP" "$STAGING/Quassel.app"
ln -s /Applications "$STAGING/Applications"

# Was ausgeliefert wird, muss signiert sein — sonst scheitert der erste Start
# beim Nutzer und die TCC-Freigaben hängen an einer instabilen Identität.
codesign --verify --deep "$STAGING/Quassel.app"

rm -f "$DMG"
hdiutil create -volname "$VOLNAME" -srcfolder "$STAGING" -format UDBZ -ov "$DMG"

SIZE="$(du -h "$DMG" | cut -f1)"
SHA="$(shasum -a 256 "$DMG" | cut -d' ' -f1)"

echo "OK: $DMG"
echo "Größe: $SIZE"
echo "SHA256: $SHA"
