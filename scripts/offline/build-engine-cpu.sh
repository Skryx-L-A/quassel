#!/usr/bin/env bash
# Baut whisper-server (CPU-only, portabel) in manylinux_2_28 (glibc 2.28) und
# sammelt Binary + nicht-System-.so + libgomp nach /out. Läuft IM Container.
set -euo pipefail
export PATH="/opt/python/cp312-cp312/bin:$PATH"
pip -q install cmake ninja >/dev/null
dnf -y install git >/dev/null 2>&1 || true
cd /tmp
git clone --depth 1 https://github.com/ggml-org/whisper.cpp wc
cd wc
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DWHISPER_BUILD_SERVER=ON >/dev/null
cmake --build build -j"$(nproc)" >/dev/null
mkdir -p /out/cpu
cp build/bin/whisper-server /out/cpu/
# alle gelinkten .so außer reinen System-Libs (glibc/ld) mitnehmen
ldd build/bin/whisper-server | awk '/=>/{print $3}' | while read -r so; do
  [ -f "$so" ] || continue
  case "$so" in
    *libc.so*|*libm.so*|*libdl.so*|*libpthread.so*|*librt.so*|*ld-linux*|*libresolv*|*libgcc_s*) ;;
    *) cp -L "$so" /out/cpu/ ;;
  esac
done
# libgomp (OpenMP) sicherheitshalber mitnehmen
find / -name 'libgomp.so*' 2>/dev/null | head -1 | xargs -r -I{} cp -L {} /out/cpu/ || true
chmod +x /out/cpu/whisper-server
echo "=== /out/cpu Inhalt ==="; ls -la /out/cpu
