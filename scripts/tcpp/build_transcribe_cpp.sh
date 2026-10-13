#!/usr/bin/env bash
# Reproducible build of transcribe-cli from transcribe.cpp at a fixed tag.
#
# Everything lands under one directory (default ~/quassel-bench-data/tcpp):
#   src/transcribe.cpp-<tag>/        shallow clone of the tag
#   build/<tag>-<backend>/           CMake build tree
#   bin/transcribe-cli-<tag>-<backend>   the binary Quassel's adapter calls
#   bin/transcribe-cli-<tag>-<backend>.json  provenance (tag, commit, flags)
# Nothing is installed globally, no sudo, no PATH changes, no models.
#
# Usage:
#   scripts/tcpp/build_transcribe_cpp.sh [--backend auto|metal|cpu|cuda|vulkan]
#       [--dest DIR] [--jobs N] [--dry-run]
#
# auto picks metal on Apple Silicon, cuda when nvcc is on PATH (Linux),
# vulkan when glslc is on PATH (Linux), cpu otherwise.
# Windows: follow upstream docs/build-windows.md with the same tag; this
# script targets macOS and Linux.
set -euo pipefail

TAG="v0.3.1"
COMMIT="3f32fbcc7bb3246851a0234263438bc3c0fa1cac"
REPO="https://github.com/handy-computer/transcribe.cpp.git"

DEST="${HOME}/quassel-bench-data/tcpp"
BACKEND="auto"
JOBS=""
DRY_RUN=0

die() { echo "build_transcribe_cpp: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --backend) [ $# -ge 2 ] || die "--backend needs a value"; BACKEND="$2"; shift 2 ;;
        --dest)    [ $# -ge 2 ] || die "--dest needs a value"; DEST="$2"; shift 2 ;;
        --jobs)    [ $# -ge 2 ] || die "--jobs needs a value"; JOBS="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

OS="$(uname -s)"
ARCH="$(uname -m)"

if [ "$BACKEND" = "auto" ]; then
    if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
        BACKEND="metal"
    elif [ "$OS" = "Linux" ] && command -v nvcc >/dev/null 2>&1; then
        BACKEND="cuda"
    elif [ "$OS" = "Linux" ] && command -v glslc >/dev/null 2>&1; then
        BACKEND="vulkan"
    else
        BACKEND="cpu"
    fi
fi

# Upstream CMakeLists.txt:124-127: Metal defaults ON on Apple Silicon, Vulkan
# and CUDA are opt-in. Every flag is set explicitly so the build does not
# depend on the host's defaults.
CMAKE_FLAGS=(-DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_TESTS=OFF
             -DTRANSCRIBE_BUILD_EXAMPLES=ON -DTRANSCRIBE_BUILD_TOOLS=OFF
             -DTRANSCRIBE_INSTALL=OFF)
case "$BACKEND" in
    metal)
        [ "$OS" = "Darwin" ] || die "metal needs macOS"
        CMAKE_FLAGS+=(-DTRANSCRIBE_METAL=ON -DTRANSCRIBE_VULKAN=OFF -DTRANSCRIBE_CUDA=OFF) ;;
    cuda)
        [ "$OS" = "Linux" ] || die "cuda is built on Linux here; Windows: upstream docs/build-windows.md"
        command -v nvcc >/dev/null 2>&1 || die "cuda needs nvcc on PATH"
        CMAKE_FLAGS+=(-DTRANSCRIBE_METAL=OFF -DTRANSCRIBE_VULKAN=OFF -DTRANSCRIBE_CUDA=ON) ;;
    vulkan)
        [ "$OS" = "Linux" ] || die "vulkan is built on Linux here; Windows: upstream docs/build-windows.md"
        CMAKE_FLAGS+=(-DTRANSCRIBE_METAL=OFF -DTRANSCRIBE_VULKAN=ON -DTRANSCRIBE_CUDA=OFF) ;;
    cpu)
        CMAKE_FLAGS+=(-DTRANSCRIBE_METAL=OFF -DTRANSCRIBE_VULKAN=OFF -DTRANSCRIBE_CUDA=OFF) ;;
    *) die "backend must be auto, metal, cpu, cuda or vulkan" ;;
esac

if [ -z "$JOBS" ]; then
    JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
fi

SRC="${DEST}/src/transcribe.cpp-${TAG}"
BUILD="${DEST}/build/${TAG}-${BACKEND}"
BIN_DIR="${DEST}/bin"
BIN="${BIN_DIR}/transcribe-cli-${TAG}-${BACKEND}"

run() {
    printf '+'; printf ' %q' "$@"; printf '\n'
    if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}

command -v git >/dev/null 2>&1 || die "git missing"
command -v cmake >/dev/null 2>&1 || die "cmake missing"

if [ ! -d "${SRC}/.git" ]; then
    run mkdir -p "${DEST}/src"
    run git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$TAG" "$REPO" "$SRC"
fi
if [ "$DRY_RUN" -eq 0 ]; then
    got="$(git -C "$SRC" rev-parse HEAD)"
    [ "$got" = "$COMMIT" ] || die "tag ${TAG} is ${got}, expected ${COMMIT} (tag moved?)"
fi

run cmake -S "$SRC" -B "$BUILD" "${CMAKE_FLAGS[@]}"
run cmake --build "$BUILD" --config Release --target transcribe-cli -j "$JOBS"

run mkdir -p "$BIN_DIR"
# Copy to a temporary name first, then rename: a half-written binary never
# appears under the final name.
run cp "${BUILD}/bin/transcribe-cli" "${BIN}.tmp"
run mv -f "${BIN}.tmp" "$BIN"

if [ "$DRY_RUN" -eq 0 ]; then
    tmp_json="${BIN}.json.tmp"
    {
        printf '{\n'
        printf '  "tag": "%s",\n' "$TAG"
        printf '  "commit": "%s",\n' "$COMMIT"
        printf '  "backend": "%s",\n' "$BACKEND"
        printf '  "os": "%s",\n' "$OS"
        printf '  "arch": "%s",\n' "$ARCH"
        printf '  "built": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '  "cmake_flags": "%s"\n' "${CMAKE_FLAGS[*]}"
        printf '}\n'
    } > "$tmp_json"
    mv -f "$tmp_json" "${BIN}.json"
    echo "built: ${BIN}"
fi
