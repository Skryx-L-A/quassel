# Building the Windows installer

> The Windows port runs as a single Qt process (system tray + floating pill +
> low-level keyboard hook) and downloads whisper.cpp + the speech model on
> first launch, so the installer stays small. Built and tested on Windows 10/11.

## Prerequisites (on a Windows machine)

- Python 3.10+ (64-bit)
- `pip install pyside6 sounddevice pyinstaller`
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (for the `.exe` installer)

## Build

From the repository root in PowerShell:

```powershell
pip install pyside6 sounddevice pyinstaller
pyinstaller windows\voxtype.spec
iscc windows\voxtype.iss
```

- `pyinstaller` produces `dist\VoxType\VoxType.exe` (the app folder).
- `iscc` wraps it into `windows\Output\VoxType-Setup-2.1.0.exe`.

## First launch

On first run VoxType downloads the matching whisper.cpp binaries
(cuBLAS build if an NVIDIA GPU is present, otherwise CPU) and a speech model
into `%LOCALAPPDATA%\VoxType`. After that it works fully offline.

## Notes

- The app is unsigned, so SmartScreen will warn on first run
  ("More info" → "Run anyway"). Code signing is a future step.
- The keyboard hook needs no admin rights. If another app blocks global
  hooks, run VoxType after it.
- Settings, dictionary and history live in `%APPDATA%\VoxType` and
  `%LOCALAPPDATA%\VoxType`.
