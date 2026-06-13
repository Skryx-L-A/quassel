#!/usr/bin/env bash
# Baut whisper-server (CUDA 12.x, portabel) in nvidia/cuda devel (UBI8, glibc 2.28).
# cmake/make/gcc aus UBI8-Repos (kein pip). libcuda.so.1 NICHT bündeln (Treiber!).
set -euo pipefail
export PATH="/usr/local/cuda/bin:$PATH"
dnf -y install gcc gcc-c++ make cmake git >/dev/null 2>&1
cmake --version | head -1
cd /tmp
git clone --depth 1 https://github.com/ggml-org/whisper.cpp wc
cd wc
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF \
      -DWHISPER_BUILD_SERVER=ON -DGGML_CUDA=1 \
      -DCMAKE_CUDA_ARCHITECTURES="61;70;75;80;86;89"
cmake --build build -j"$(nproc)" --target whisper-server
mkdir -p /out/cuda
cp build/bin/whisper-server /out/cuda/
ldd build/bin/whisper-server | awk '/=>/{print $3}' | while read -r so; do
  [ -f "$so" ] || continue
  case "$so" in
    *libc.so*|*libm.so*|*libdl.so*|*libpthread.so*|*librt.so*|*ld-linux*|*libresolv*|*libgcc_s*) ;;
    *libcuda.so*) ;;   # Treiber-Lib: MUSS vom Zielsystem kommen
    *) cp -L "$so" /out/cuda/ ;;
  esac
done
for pat in libcudart libcublas libcublasLt; do
  find /usr/local/cuda -name "${pat}.so*" 2>/dev/null | sort | tail -1 | xargs -r -I{} cp -L {} /out/cuda/
done
find / -name 'libgomp.so*' 2>/dev/null | head -1 | xargs -r -I{} cp -L {} /out/cuda/ || true
chmod +x /out/cuda/whisper-server
echo "=== /out/cuda ==="; ls -la /out/cuda
