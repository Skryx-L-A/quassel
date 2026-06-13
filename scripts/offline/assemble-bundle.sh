#!/usr/bin/env bash
# ============================================================================
# Fügt das portable Linux-Offline-Komplettpaket zusammen und splittet es in
# GitHub-taugliche Teile (< 2 GiB). Erwartet die vorgebauten Artefakte unter
# $BUILD (python/, engine/{cpu,cuda,tools}/, models/). Vom Repo-Root ausführen.
# ============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="${BUILD:-$HOME/quassel-offline-build}"
STAGE="$BUILD/stage"
NAME="quassel-linux-x86_64"
DEST="$STAGE/$NAME"
OUT="${OUT:-$BUILD/dist}"

echo "==> Staging aufräumen: $DEST"
rm -rf "$STAGE"; mkdir -p "$DEST" "$OUT"

echo "==> Python + Engine + Tools + Modelle kopieren"
cp -a "$BUILD/python"        "$DEST/python"
mkdir -p "$DEST/engine"
cp -a "$BUILD/engine/cpu"    "$DEST/engine/cpu"
[ -x "$BUILD/engine/cuda/whisper-server" ] && cp -a "$BUILD/engine/cuda" "$DEST/engine/cuda" \
    || echo "    ! CUDA-Engine fehlt — Paket wird CPU-only"
cp -a "$BUILD/engine/tools"  "$DEST/tools"
cp -a "$BUILD/models"        "$DEST/models"

echo "==> App-Quelltext (quassel/) + Assets"
cp -a "$REPO/quassel" "$DEST/quassel"
find "$DEST/quassel" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
cp -a "$REPO/assets" "$DEST/assets"

echo "==> Installer/Uninstaller/README"
install -m 755 "$REPO/scripts/offline/bundle-files/install.sh"   "$DEST/install.sh"
install -m 755 "$REPO/scripts/offline/bundle-files/uninstall.sh" "$DEST/uninstall.sh"
install -m 644 "$REPO/scripts/offline/bundle-files/README.txt"   "$DEST/README.txt"

echo "==> Größe des entpackten Bundles:"; du -sh "$DEST"

echo "==> tar.gz erstellen + in 1900-MiB-Teile splitten"
rm -f "$OUT/${NAME}".tar.gz.part-* "$OUT/${NAME}.tar.gz"
( cd "$STAGE" && tar -czf - "$NAME" ) | split -b 1900M -d -a 2 - "$OUT/${NAME}.tar.gz.part-"
echo "==> Teile + Prüfsummen:"
( cd "$OUT" && ls -lh ${NAME}.tar.gz.part-* && sha256sum ${NAME}.tar.gz.part-* > "${NAME}.sha256" && cat "${NAME}.sha256" )
echo "==> Fertig. Dist unter: $OUT"
