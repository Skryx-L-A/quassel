#!/usr/bin/env bash
# Baut ydotool/ydotoold (uinput-Injektion) + wl-copy/wl-paste (Wayland-Clipboard)
# portabel in manylinux_2_28 (glibc 2.28). Läuft IM Container.
set -uo pipefail
export PATH="/opt/python/cp312-cp312/bin:$PATH"
dnf -y install git wayland-devel libxkbcommon-devel >/dev/null 2>&1 || true
pip -q install cmake ninja meson >/dev/null
mkdir -p /out/tools
gather() { # $1 = binary -> kopiere + nicht-System-.so
  cp -L "$1" /out/tools/
  ldd "$1" | awk '/=>/{print $3}' | while read -r so; do
    [ -f "$so" ] || continue
    case "$so" in
      *libc.so*|*libm.so*|*libdl.so*|*libpthread.so*|*librt.so*|*ld-linux*|*libresolv*|*libgcc_s*) ;;
      *) cp -L "$so" /out/tools/ 2>/dev/null ;;
    esac
  done
}

echo "### ydotool ###"
cd /tmp && git clone --depth 1 https://github.com/ReimuNotMoe/ydotool yd && cd yd
# manpage-Bau (scdoc) entfernen, damit kein scdoc nötig ist
sed -i '/manpage/d; /scdoc/Id' CMakeLists.txt 2>/dev/null || true
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1
cmake --build build --target ydotool ydotoold -j"$(nproc)" 2>&1 | tail -3
for b in build/ydotool build/ydotoold; do [ -x "$b" ] && gather "$b" && echo "  ok: $b"; done

echo "### wl-clipboard ###"
cd /tmp && git clone --depth 1 https://github.com/bugaevc/wl-clipboard wl && cd wl
meson setup build >/dev/null 2>&1 && ninja -C build 2>&1 | tail -3
for b in build/src/wl-copy build/src/wl-paste; do [ -x "$b" ] && gather "$b" && echo "  ok: $b"; done

chmod +x /out/tools/* 2>/dev/null
echo "=== /out/tools ==="; ls -la /out/tools
