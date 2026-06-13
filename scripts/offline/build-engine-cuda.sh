#!/usr/bin/env bash
# whisper-server CUDA 12.x, portabel (UBI8/glibc 2.28). Nutzt gcc-toolset-12
# (std::filesystem) + CUDA-Treiber-Stub. libcuda.so.1 NICHT bündeln (Treiber).
set -euo pipefail
export PATH="/usr/local/cuda/bin:$PATH"
dnf -y install make cmake git gcc-toolset-12 >/dev/null 2>&1
source /opt/rh/gcc-toolset-12/enable
STUB=/usr/local/cuda/lib64/stubs
# Treiber-Stub heißt libcuda.so (ohne .1-SONAME) -> Symlink anlegen, damit der
# Linker libcuda.so.1 beim finalen Exe-Link auflösen kann (-rpath-link).
ln -sf "$STUB/libcuda.so" "$STUB/libcuda.so.1" 2>/dev/null || true
export LIBRARY_PATH="$STUB:${LIBRARY_PATH:-}"
gcc --version | head -1; cmake --version | head -1
WC=/work/wc
[ -d "$WC/.git" ] || git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WC"
cd "$WC"
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF \
      -DWHISPER_BUILD_SERVER=ON -DGGML_CUDA=1 \
      -DCMAKE_CUDA_ARCHITECTURES="61;70;75;80;86;89" \
      -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,$STUB" \
      -DCMAKE_SHARED_LINKER_FLAGS="-Wl,-rpath-link,$STUB"
cmake --build build -j"$(nproc)" --target whisper-server
mkdir -p /out/cuda
cp build/bin/whisper-server /out/cuda/
ldd build/bin/whisper-server | awk '/=>/{print $3}' | while read -r so; do
  [ -f "$so" ] || continue
  case "$so" in
    *libc.so*|*libm.so*|*libdl.so*|*libpthread.so*|*librt.so*|*ld-linux*|*libresolv*|*libgcc_s*) ;;
    *libcuda.so*) ;;   # Treiber-Lib vom Zielsystem
    *) cp -L "$so" /out/cuda/ ;;
  esac
done
for pat in libcudart libcublas libcublasLt; do
  find /usr/local/cuda -name "${pat}.so*" 2>/dev/null | sort | tail -1 | xargs -r -I{} cp -L {} /out/cuda/
done
# libstdc++ aus gcc-toolset-12 (neuer als evtl. System) mitnehmen
find /opt/rh/gcc-toolset-12 -name 'libstdc++.so.6*' 2>/dev/null | sort | tail -1 | xargs -r -I{} cp -L {} /out/cuda/
find /opt/rh/gcc-toolset-12 -name 'libgomp.so*' 2>/dev/null | head -1 | xargs -r -I{} cp -L {} /out/cuda/
chmod +x /out/cuda/whisper-server
echo "=== /out/cuda ==="; ls -la /out/cuda
