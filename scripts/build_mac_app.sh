#!/usr/bin/env bash
# Quassel als macOS-.app bauen (PyInstaller onedir + ad-hoc-Signatur).
# Aufruf aus dem Repo-Root:  scripts/build_mac_app.sh
# Ergebnis: dist/Quassel.app
#
# Voraussetzungen: .venv mit den App-Abhängigkeiten (PySide6, sounddevice,
# pyobjc, ...) und ein Metal-Build von whisper.cpp unter
# vendor/whisper.cpp/build/bin (whisper-server + libwhisper/libggml-dylibs).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
APP="$REPO/dist/Quassel.app"

[ -x "$PY" ] || { echo "FEHLER: $PY fehlt (venv anlegen, Abhängigkeiten installieren)"; exit 1; }
[ -x "$REPO/vendor/whisper.cpp/build/bin/whisper-server" ] || {
    echo "FEHLER: vendor/whisper.cpp/build/bin/whisper-server fehlt (erst whisper.cpp bauen)"; exit 1; }

"$PY" -c 'import PyInstaller' 2>/dev/null || uv pip install --python "$PY" pyinstaller

cd "$REPO"
"$PY" -m PyInstaller --noconfirm --clean packaging/macos/Quassel.spec

# Der gebündelte whisper-server trägt nur den absoluten rpath des
# Build-Ordners. @loader_path ergänzen, damit er seine dylibs auch findet,
# wenn das Repo nicht (mehr) existiert — die dylibs liegen daneben.
SERVER="$APP/Contents/Frameworks/whisper/whisper-server"
if ! otool -l "$SERVER" | grep -q 'path @loader_path (offset'; then
    install_name_tool -add_rpath @loader_path "$SERVER"
fi

# Ad-hoc-Signatur (kein Developer-Account): TCC-Grants (Mikrofon,
# Bedienungshilfen, Input Monitoring) überleben so Rebuilds besser als
# komplett unsigniert. install_name_tool hat die Signatur des Servers
# invalidiert; --force --deep signiert alles neu.
codesign --force --deep -s - "$APP"
codesign --verify --deep "$APP"

echo "OK: $APP"
