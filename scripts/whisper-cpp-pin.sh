# shellcheck shell=bash
# Wird von anderen Skripten eingelesen; die Variablen gelten dort.
# shellcheck disable=SC2034
# Fester whisper.cpp-Stand fuer alle Linux-Baeume (install.sh, die
# Engine-Pakete aus .github/workflows/engine-packages.yml, scripts/offline/,
# packaging/aur/PKGBUILD). Einzige Quelle: hier aendern, nirgends sonst.
#
# v1.9.5 (2026-10-06) ist das neueste getaggte Release (Wartungsrelease mit
# ggml 0.26.0; whisper-server/server.cpp und seine Optionen unveraendert zu
# v1.9.4). Es ist die aktuelle CUDA-/Vulkan-Fassung von ggml und hat einen
# annotierten Tag. Der Commit wird beim Klonen gegengeprueft: verschiebt
# jemand den Tag, bricht der Bau ab statt fremden Code zu bauen. Der macOS-
# Build (Cask, vendor/whisper.cpp) ist davon unabhaengig.
WHISPER_CPP_URL="https://github.com/ggml-org/whisper.cpp"
WHISPER_CPP_TAG="v1.9.5"
WHISPER_CPP_COMMIT="d1be6fde11ac6e0407606b4e42fe72d34add8037"
