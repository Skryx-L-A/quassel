# Building the Windows installer

> The Windows port runs as a single Qt process (system tray + floating pill +
> Raw-Input keyboard listener). The listener sits outside the system input
> chain, so a busy or even hung Quassel can never stall the keyboard.
> whisper.cpp + the speech model are downloaded on first launch, so the
> installer stays small. Built and tested on Windows 10/11.

## Prerequisites (on a Windows machine)

- Python 3.13 (64-bit) — **not 3.14**, the windowed exe crashes with it
- `pip install pyside6 sounddevice pyinstaller`
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (for the `.exe` installer)

## Build

From the repository root in PowerShell:

```powershell
py -3.13 -m pip install pyside6 sounddevice pyinstaller
py -3.13 -m PyInstaller --noconfirm --clean --distpath windows\dist --workpath windows\build windows\quassel.spec
iscc windows\quassel.iss
```

Always build with `--clean` — PyInstaller otherwise caches stale analyses.

- `PyInstaller` produces `windows\dist\Quassel\Quassel.exe` (the app folder).
- `iscc` wraps it into `windows\Output\Quassel-Setup-2.1.0.exe`.

## First launch

On first run Quassel downloads the matching whisper.cpp binaries
(cuBLAS build if an NVIDIA GPU is present, otherwise CPU) and a speech model
into `%LOCALAPPDATA%\Quassel`. After that it works fully offline.

## Notes

- The app is unsigned, so SmartScreen will warn on first run
  ("More info" → "Run anyway"). Code signing is a future step.
- The keyboard listener uses Raw Input (no hook, no admin rights) and only
  observes the hotkey chord; it never suppresses or delays keystrokes.
- Settings, dictionary and history live in `%APPDATA%\Quassel` and
  `%LOCALAPPDATA%\Quassel`.
- Troubleshooting: `%LOCALAPPDATA%\Quassel\crash.log` (startup crashes) and
  `%LOCALAPPDATA%\Quassel\debug.log` (per-dictation timing of recording,
  inference and paste).
