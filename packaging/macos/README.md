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
| ffmpeg | NICHT im Bundle — Erkennung beim Start, Tray-Hinweis `brew install ffmpeg` |

Die Kindprozesse laufen gefroren als Subkommando derselben exe
(`Quassel daemon`, `Quassel center`) statt `python -m ...`
(Dispatch in `packaging/macos/quassel_mac_main.py`).

## Erster Start (TCC-Freigaben)

Beim ersten Start der .app fragt macOS pro Bundle-Identität neu:

- **Mikrofon** (Aufnahme über ffmpeg/AVFoundation)
- **Bedienungshilfen** + **Eingabeüberwachung** (CGEventTap-Hotkey, Einfügen)

Systemeinstellungen > Datenschutz & Sicherheit, jeweils Quassel.app freigeben,
danach die App neu starten. Abstürze landen in `~/Library/Logs/Quassel/crash.log`.
