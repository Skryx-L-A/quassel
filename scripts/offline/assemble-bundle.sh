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

# CUDA-Runtime (cudart/cublas/cublasLt) in einen Fallback-Unterordner verschieben:
# der Wrapper hängt ihn nur ein, wenn das System keine passende CUDA-Runtime hat
# (vorhandenes/neueres System-CUDA bleibt so unangetastet und bevorzugt).
if [ -d "$DEST/engine/cuda" ]; then
    mkdir -p "$DEST/engine/cuda/cudart-fallback"
    for pat in 'libcudart.so*' 'libcublas.so*' 'libcublasLt.so*'; do
        find "$DEST/engine/cuda" -maxdepth 1 -name "$pat" \
            -exec mv {} "$DEST/engine/cuda/cudart-fallback/" \;
    done
    echo "    CUDA-Runtime -> engine/cuda/cudart-fallback/ (nur Fallback)"
fi

echo "==> App-Quelltext (quassel/) + Assets"
cp -a "$REPO/quassel" "$DEST/quassel"
find "$DEST/quassel" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
cp -a "$REPO/assets" "$DEST/assets"

echo "==> Installer/Uninstaller/README"
install -m 755 "$REPO/scripts/offline/bundle-files/install.sh"   "$DEST/install.sh"
install -m 755 "$REPO/scripts/offline/bundle-files/uninstall.sh" "$DEST/uninstall.sh"
install -m 644 "$REPO/scripts/offline/bundle-files/README.txt"   "$DEST/README.txt"

echo "==> Größe des entpackten Bundles:"; du -sh "$DEST"

echo "==> 1) Einzel-Gesamtpaket (für eigene Website — Größe egal)"
rm -f "$OUT/${NAME}".tar.gz "$OUT/${NAME}".tar.gz.part-* "$OUT/${NAME}".sha256
( cd "$STAGE" && tar -I 'gzip -1' -cf "$OUT/${NAME}.tar.gz" "$NAME" )

echo "==> 2) Split in < 2-GiB-Teile (für GitHub-Release)"
split -b 1900M "$OUT/${NAME}.tar.gz" "$OUT/${NAME}.tar.gz.part-"

echo "==> Größen + Prüfsummen (Einzelpaket UND Teile):"
( cd "$OUT" \
  && ls -lh ${NAME}.tar.gz ${NAME}.tar.gz.part-* \
  && sha256sum ${NAME}.tar.gz ${NAME}.tar.gz.part-* > "${NAME}.sha256" \
  && cat "${NAME}.sha256" )
echo "==> Fertig. Einzelpaket: $OUT/${NAME}.tar.gz   GitHub-Teile: $OUT/${NAME}.tar.gz.part-*"
