# shellcheck shell=bash
# Hilfsfunktionen fuer install.sh (per `source` geladen, keine Nebenwirkungen).
# Getrennt, damit sie in tests/test_install_scripts.py mit Attrappen fuer
# curl, git und nvidia-smi geprueft werden koennen.
#
# Erwartet vom Aufrufer: say/ok/warn und die Variablen SRC und DATA.
# Alle Funktionen melden Fehler ueber den Rueckgabewert (kein `exit`), weil
# sie in `if`-Bedingungen laufen, in denen `set -e` nicht greift.

QUASSEL_SLUG="${QUASSEL_SLUG:-Skryx-L-A/quassel}"
QUASSEL_API="${QUASSEL_API:-https://api.github.com}"
QUASSEL_DL="${QUASSEL_DL:-https://github.com}"

# Release-Tag, zu dem diese Quellen gehoeren: QUASSEL_RELEASE_TAG (setzt der
# Online-Installer) oder die Version aus quassel/__init__.py.
lib_release_tag() {
    local tag="${QUASSEL_RELEASE_TAG:-}" ver
    if [[ -z "$tag" ]]; then
        ver="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$1/quassel/__init__.py" 2>/dev/null | head -1)"
        [[ -n "$ver" ]] && tag="v$ver"
    fi
    [[ "$tag" =~ ^v?[0-9]+(\.[0-9]+){1,3}([.-][A-Za-z0-9]+)?$ ]] || return 1
    printf '%s\n' "$tag"
}

# Liegt das Asset im Release? Fragt die GitHub-API; Netzfehler zaehlen als "nein".
prebuilt_asset_listed() {
    local tag="$1" asset="$2" json
    json="$(curl -fsSL --max-time 30 -H 'Accept: application/vnd.github+json' \
        "$QUASSEL_API/repos/$QUASSEL_SLUG/releases/tags/$tag" 2>/dev/null)" || return 1
    printf '%s\n' "$json" | grep -Eq "\"name\" *: *\"${asset//./\\.}\""
}

lib_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# Erwartete Pruefsumme: Datei <asset>.sha256, sonst Zeile in SHA256SUMS.txt.
prebuilt_expected_sum() {
    local base="$1" asset="$2" sum
    sum="$(curl -fsSL --max-time 30 "$base/$asset.sha256" 2>/dev/null | awk '{print $1; exit}' || true)"
    if [[ ! "$sum" =~ ^[0-9a-f]{64}$ ]]; then
        sum="$(curl -fsSL --max-time 30 "$base/SHA256SUMS.txt" 2>/dev/null \
            | awk -v a="$asset" '$2 == a || $2 == "*" a {print $1; exit}' || true)"
    fi
    [[ "$sum" =~ ^[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$sum"
}

# Asset laden, Pruefsumme verlangen, Archiv auf Pfade ausserhalb des Ziels
# pruefen und nach $3/<variante>/ entpacken. $4 = Variante (cpu|cuda).
prebuilt_fetch() {
    local tag="$1" asset="$2" outdir="$3" evar="$4" base want got part listing
    base="$QUASSEL_DL/$QUASSEL_SLUG/releases/download/$tag"
    want="$(prebuilt_expected_sum "$base" "$asset")" \
        || { warn "Keine Prüfsumme zu $asset im Release $tag – wird nicht verwendet."; return 1; }
    mkdir -p "$outdir" || return 1
    part="$outdir/$asset.part"
    rm -f "$part"
    curl -L --fail --progress-bar -o "$part" "$base/$asset" \
        || { rm -f "$part"; warn "Download von $asset fehlgeschlagen."; return 1; }
    got="$(lib_sha256 "$part")"
    if [[ "$got" != "$want" ]]; then
        rm -f "$part"; warn "Prüfsumme von $asset stimmt nicht."; return 1
    fi
    listing="$(tar -tzf "$part" 2>/dev/null)" \
        || { rm -f "$part"; warn "$asset ist kein lesbares Archiv."; return 1; }
    if printf '%s\n' "$listing" | grep -Eq '(^/|(^|/)\.\.(/|$))' \
        || printf '%s\n' "$listing" | grep -Evq "^$evar(/|\$)"; then
        rm -f "$part"; warn "$asset enthält unerwartete Pfade."; return 1
    fi
    rm -rf "${outdir:?}/$evar"
    tar -xzf "$part" -C "$outdir" || { rm -f "$part"; return 1; }
    rm -f "$part"
    [[ -x "$outdir/$evar/whisper-server" ]]
}

# Vorgebaute Engine einrichten. Setzt SERVER_BIN_PATH, HAS_GPU, ENGINE_DESC.
# Rueckgabe 1 = nicht moeglich, der Aufrufer baut dann aus dem Quelltext.
install_prebuilt_engine() {
    local tag evars evar edir asset stage found=""
    if [[ "$(uname -m)" != "x86_64" ]]; then
        warn "Vorgebaute Engines gibt es nur für x86_64 – baue aus dem Quelltext."
        return 1
    fi
    if ! tag="$(lib_release_tag "$SRC")"; then
        warn "Release nicht bestimmbar – baue aus dem Quelltext."
        return 1
    fi
    evars="cpu"
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        evars="cuda cpu"
    fi
    for evar in $evars; do
        asset="quassel-engine-linux-$evar-x86_64.tar.gz"
        edir="$DATA/engine/$evar"
        if [[ -x "$edir/whisper-server" && "$(cat "$edir/.release" 2>/dev/null || true)" == "$tag" ]]; then
            found="$evar"; break
        fi
        if prebuilt_asset_listed "$tag" "$asset"; then
            echo "    Lade vorgebaute Engine ($evar, $tag)…"
            stage="$DATA/engine/.new"
            rm -rf "$stage"
            if prebuilt_fetch "$tag" "$asset" "$stage" "$evar"; then
                rm -rf "$edir"
                mkdir -p "$DATA/engine" && mv "$stage/$evar" "$edir" || return 1
                printf '%s\n' "$tag" > "$edir/.release"
                rm -rf "$stage"
                found="$evar"; break
            fi
            rm -rf "$stage"
        elif [[ -x "$edir/whisper-server" ]]; then
            warn "Release $tag hat kein Paket $asset – behalte die vorhandene Engine ($evar)."
            found="$evar"; break
        fi
    done
    if [[ -z "$found" ]]; then
        warn "Release $tag enthält keine nutzbare Engine ($evars) – baue aus dem Quelltext."
        return 1
    fi
    edir="$DATA/engine/$found"
    # Wrapper: gebuendelte Libs prozess-lokal (System bleibt unangetastet);
    # der NVIDIA-Treiber kommt zur Laufzeit immer vom Zielsystem.
    cat > "$edir/run-server.sh" <<'WRAP'
#!/usr/bin/env bash
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$here:${LD_LIBRARY_PATH:-}"
exec "$here/whisper-server" "$@"
WRAP
    chmod +x "$edir/run-server.sh"
    # Rückgabe an install.sh, das diese Datei einliest.
    # shellcheck disable=SC2034
    SERVER_BIN_PATH="$edir/run-server.sh"
    # shellcheck disable=SC2034
    ENGINE_DESC="vorgebaut: $found"
    # shellcheck disable=SC2034
    if [[ "$found" == "cuda" ]]; then HAS_GPU=1; else HAS_GPU=0; fi
    return 0
}

# whisper.cpp auf den gepinnten Tag bringen (Klon oder bestehender Klon).
# Verlangt den erwarteten Commit; sonst Rueckgabe 1.
whisper_checkout() {
    local dir="$1" head
    if [[ -d "$dir" && ! -d "$dir/.git" ]]; then
        rm -rf "$dir"          # Rest eines abgebrochenen Klons, kein Nutzerinhalt
    fi
    if [[ ! -d "$dir/.git" ]]; then
        git clone --depth 1 --branch "$WHISPER_CPP_TAG" "$WHISPER_CPP_URL" "$dir" \
            || return 1
    fi
    head="$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
    if [[ "$head" != "$WHISPER_CPP_COMMIT" ]]; then
        git -C "$dir" fetch --depth 1 origin \
            "refs/tags/$WHISPER_CPP_TAG:refs/tags/$WHISPER_CPP_TAG" || return 1
        git -C "$dir" -c advice.detachedHead=false checkout --force \
            "refs/tags/$WHISPER_CPP_TAG" || return 1
        head="$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
    fi
    if [[ "$head" != "$WHISPER_CPP_COMMIT" ]]; then
        warn "whisper.cpp $WHISPER_CPP_TAG zeigt auf $head statt $WHISPER_CPP_COMMIT – Abbruch."
        return 1
    fi
}

# Zeile fuer die Neubau-Entscheidung: gepinnter Commit plus angeforderte Flags.
whisper_build_stamp() {
    printf '%s %s\n' "$WHISPER_CPP_COMMIT" "$*"
}

# KEY=WERT in einer env-Datei setzen (erste Fundstelle ersetzen, doppelte
# entfernen, sonst anhaengen); atomar per Temp-Datei im selben Ordner.
set_env_key() {
    local file="$1" key="$2" value="$3" tmp
    tmp="$(mktemp "$file.XXXXXX")" || return 1
    if ! K="$key" V="$value" awk '
        index($0, ENVIRON["K"] "=") == 1 {
            if (!done) { print ENVIRON["K"] "=" ENVIRON["V"]; done = 1 }
            next
        }
        { print }
        END { if (!done) print ENVIRON["K"] "=" ENVIRON["V"] }
    ' "$file" > "$tmp"; then
        rm -f "$tmp"; return 1
    fi
    chmod 644 "$tmp"
    mv "$tmp" "$file"
}

# MODEL_PATH aus einer env-Datei (letzter Eintrag gilt wie bei systemd).
env_value() {
    grep "^$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- || true
}
