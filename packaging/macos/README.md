# Quassel als macOS-.app bauen

Ergebnis: `dist/Quassel.app` — eine Menüleisten-App (kein Dock-Icon, `LSUIElement`),
die whisper-server (Metal), den Hotkey-Daemon und die Qt-Pille als eine App startet.
Ad-hoc signiert (`codesign -s -`): kein Apple-Developer-Account nötig; die
TCC-Freigaben (Mikrofon, Bedienungshilfen, Eingabeüberwachung) überleben so
Rebuilds besser als bei einer unsignierten App.

## Voraussetzungen

- `.venv` im Repo-Root mit den App-Abhängigkeiten (PySide6, sounddevice, pyobjc, ...)
- Metal-Build von whisper.cpp: `vendor/whisper.cpp/build/bin/whisper-server`
  samt `libwhisper`/`libggml`-dylibs (siehe MACOS-PORT-BRIEF.md)
- PyInstaller wird bei Bedarf automatisch in die venv installiert

## Bauen

```sh
scripts/build_mac_app.sh
```

Das Skript baut per PyInstaller (`packaging/macos/Quassel.spec`, onedir),
ergänzt am gebündelten whisper-server den rpath `@loader_path` (die dylibs
liegen im Bundle daneben) und signiert die .app ad-hoc.

## Was ist im Bundle — und was nicht

| Teil | Ort |
|---|---|
| whisper-server + dylibs | `Contents/Resources/whisper/` (Lookup: `server_mac.bundled_server_bin()`) |
| Assets (Icons, Start/Stopp-Töne) | `Contents/Resources/assets/` |
| ggml-Modelle | NICHT im Bundle — `~/Library/Application Support/Quassel/models`, lädt die App selbst |
| ffmpeg | NICHT im Bundle und für die Aufnahme NICHT nötig (die läuft über sounddevice/CoreAudio). Nur die Datei-Transkription von Nicht-WAV-Formaten braucht ffmpeg; der Tray-Hinweis `brew install ffmpeg` erscheint nur, wenn das alte Backend per `QUASSEL_MAC_AUDIO=ffmpeg` erzwungen wird |

Die Kindprozesse laufen gefroren als Subkommando derselben exe
(`Quassel daemon`, `Quassel center`) statt `python -m ...`
(Dispatch in `packaging/macos/quassel_mac_main.py`).

## Erster Start (TCC-Freigaben)

Beim ersten Start der .app fragt macOS pro Bundle-Identität neu:

- **Mikrofon** (Aufnahme über sounddevice/CoreAudio)
- **Bedienungshilfen** + **Eingabeüberwachung** (CGEventTap-Hotkey, Einfügen)

Systemeinstellungen > Datenschutz & Sicherheit, jeweils Quassel.app freigeben,
danach die App neu starten. Abstürze landen in `~/Library/Logs/Quassel/crash.log`.

## DMG bauen (Auslieferung)

```sh
scripts/build_mac_dmg.sh
```

Baut aus `dist/Quassel.app` ein `dist/Quassel-macOS-arm64.dmg` (`hdiutil`,
Format UDBZ, Volume „Quassel", `/Applications`-Symlink daneben — normales
Drag-and-drop-Layout). Gibt am Ende Größe und SHA256 des Images aus; die
SHA256 wird für den Homebrew-Cask (`packaging/homebrew/Casks/quassel.rb`) und
optional eine `.sha256`-Beilage zum Release-Asset gebraucht. Bricht sauber ab,
wenn `dist/Quassel.app` fehlt.

## Ein-Zeilen-Installer

```sh
curl -fsSL https://github.com/Skryx-L-A/quassel/releases/latest/download/quassel-install-macos.sh | bash
```

`scripts/quassel-install-macos.sh` lädt das DMG des neuesten Release, prüft
optional dessen SHA256 gegen eine mitgelieferte `.sha256`-Datei, mountet es,
sichert eine vorhandene `/Applications/Quassel.app` nach
`~/.local/trash-snapshots/<Datum>-quassel-app/`, kopiert die neue Version nach
`/Applications`, entfernt das Quarantäne-Flag (`xattr -dr com.apple.quarantine`)
und hängt das Image wieder aus. Für Tests/Entwicklung lässt sich die Quelle
per `QUASSEL_DMG_URL=file:///pfad/zu/Quassel-macOS-arm64.dmg` überschreiben,
ganz ohne Internet.

## Homebrew

```sh
brew install --cask skryx-l-a/quassel/quassel
```

Cask-Quelle: `packaging/homebrew/Casks/quassel.rb`, Tap-Repo-Aufbau und
Release-Checkliste (Version/SHA256 aktualisieren) in
`packaging/homebrew/README.md`. Der Cask entfernt das Quarantäne-Flag selbst
(`postflight`) — hier gibt es dadurch keinen Gatekeeper-Dialog, anders als bei
DMG/Installer.
