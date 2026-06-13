# Building the Windows installer

> The Windows port runs as a single Qt process (system tray + floating pill +
> Raw-Input keyboard listener). The listener sits outside the system input
> chain, so a busy or even hung Quassel can never stall the keyboard.
> whisper.cpp + the speech model are downloaded on first launch, so the
> installer stays small. Built and tested on Windows 10/11.

## Prerequisites (on a Windows machine)

- Python 3.13 (64-bit) — **not 3.14**, the windowed exe crashes with it
- `pip install pyside6 sounddevice pyinstaller pycaw winrt-Windows.Media.Control winrt-Windows.Foundation`
  (pycaw pulls comtypes for the mute-all path; the winrt packages drive the
  pause-media path via SMTC — both get bundled into the offline package)
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

## Two ways to ship it

There are two distributions, both of which bundle *everything* so the user
never has to download anything a second time:

1. **Online installer** (`Quassel-Setup.exe`, ~37 MB) — the Inno Setup `.exe`
   above. By default its install step (`Quassel.exe --setup`) is **lean**: it
   downloads only the engine matching the hardware (cuBLAS on NVIDIA, else
   OpenBLAS) and **one** model (`hwdetect.default_model_for_hardware()`), with a
   bilingual progress dialog. A wizard checkbox "Download everything now for
   full offline use" runs `--setup --all` instead, which fetches all engines
   (~458 MB) and all five models (~3.8 GB).
2. **Offline all-in-one** (`Quassel-Offline-Windows.exe` + `.7z.001/.002/...`)
   — a self-extracting multi-volume 7-Zip archive that already contains the
   app plus the whole payload (all engines + all five models, ~4.3 GB). No
   internet needed at all.

### Building the offline all-in-one

Assemble a payload tree (downloaded once) and wrap the app + payload in a
self-extracting multi-volume archive (volumes stay < 2 GB for GitHub):

```
payload/models/ggml-{tiny,base,small,medium,large-v3-turbo}.bin
payload/engines/cpu/whisper-bin-x64.zip
payload/engines/blas/whisper-blas-bin-x64.zip
payload/engines/cublas/whisper-cublas-12.4.0-bin-x64.zip
```

```powershell
# stage = <app dir> + payload + the bilingual readme
robocopy windows\dist\Quassel  stage\Quassel  /E
robocopy payload               stage\payload  /E
# 7z.sfx is the GUI SFX module shipped with 7-Zip
cd stage
7z a -v1900m -mx=1 -sfx7z.sfx <out>\Quassel-Offline-Windows.exe Quassel payload <readme>
```

Ship all parts (`Quassel-Offline-Windows.exe` and every `…7z.001`, `…7z.002`,
… volume) together; the user double-clicks the `.exe` and it finds the volumes
next to it.

## First launch / first run setup

Provisioning is the same code in both distributions (`quassel.win.server`):
it prefers an **offline bundle** — a `payload/` folder next to the exe (the
offline package) or pointed to by the `QUASSEL_BUNDLE` environment variable —
and only downloads when no bundle is present (the online installer). It
activates the GPU-matched engine (NVIDIA -> cuBLAS, else OpenBLAS, CPU as a
fallback) into `…\Quassel\whisper-bin` and selects the default model from the
hardware (`quassel.hwdetect`) in `%LOCALAPPDATA%\Quassel\models`.

`provision()` takes a `full` flag. **Lean** (default, the online installer's
plain `--setup`): only the matching engine and that one hardware-chosen model.
**Full** (`--setup --all`, the installer's "full offline" checkbox, and always
the offline all-in-one package since its bundle has everything): all engines
and all five models, so later model switching needs no download. Either way it
only fills gaps and never overwrites an existing — possibly newer — engine or
model that is already there.

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
