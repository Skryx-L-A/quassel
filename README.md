<img src="assets/quassel.svg" width="92" align="left" alt="Quassel logo">

# Quassel — local, private voice typing

**Hold `Ctrl+Meta` (Ctrl+Windows key), speak, release — your words are typed
wherever your cursor is.** A clean, minimal pill at the bottom of your screen
shows a live waveform and a live transcript while you speak — inspired by the
best commercial dictation apps, but **100 % offline and open source**: speech
recognition runs locally via [whisper.cpp](https://github.com/ggml-org/whisper.cpp).
No cloud, no account, no subscription, no telemetry.

**Linux** and **Windows** (Windows build is fresh — see
[windows/build.md](windows/build.md)). Fully offline, no cloud, no account.

<p align="center"><img src="assets/screenshots/hero.png" alt="Quassel — type with your voice, privately. Offline voice typing for Linux and Windows." width="820"></p>

## Features

- **Push-to-talk:** hold `Ctrl+Meta`, talk, release → text is pasted at the cursor
- **Hands-free mode:** double-tap `Ctrl+Meta`, speak freely, press once → text is pasted
- **Live preview:** see the transcript in the pill *while* you're still speaking
- **Minimal pill overlay** with a real microphone waveform — click it to open
  the control center; resizable, translucent, or turn it off entirely
- **Control center** for everything — no config files needed: hotkey, language,
  Whisper model (with in-app download), microphone, personal dictionary,
  history, autostart
- **Spoken punctuation & commands:** "comma", "new line", "scratch that" —
  German and English
- **Personal dictionary:** teach it names and jargon it would otherwise misspell
- **Local history** (optional): your last 50 dictations, one click to copy
- **Works everywhere:** any GUI app *and* terminals; Wayland and X11
- **Keyboard-layout safe:** pastes via clipboard — QWERTZ/AZERTY umlauts survive
- **GPU accelerated:** NVIDIA (CUDA) and AMD/Intel (Vulkan) auto-detected
- **Bilingual UI:** English and German, follows your system language

## Why can't I just bind a normal shortcut?

No desktop environment can bind a **modifier-only** combo like `Ctrl+Meta`.
Quassel ships a small daemon that watches the keyboard at the evdev level
(no root needed, via the `input` group) and drives the pipeline:
record → transcribe locally → paste.

## Platforms

- **Linux** — systemd, Wayland or X11, PipeWire/PulseAudio. Install via `./install.sh`.
- **Windows 10/11** — single-exe app (tray + pill + global hotkey); first launch
  downloads whisper.cpp + model. Build the installer with
  [windows/build.md](windows/build.md).

## Requirements

- Linux with **systemd** (Fedora/Nobara, Ubuntu/Debian, Arch, openSUSE, …)
- **PipeWire** (or PulseAudio), **Wayland or X11**, a microphone
- Optional: NVIDIA GPU (CUDA toolkit) or any Vulkan-capable GPU → much faster

## Install

### Windows 10 / 11

**Easiest — [download from quassel-voice.netlify.app](https://quassel-voice.netlify.app):**
one click, it picks the right file for your system.

Or paste this into **PowerShell or cmd** (the same line works in both) — it
downloads the installer and opens the setup wizard right away:

```
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/Skryx-L-A/quassel/releases/latest/download/Quassel-Setup.exe -OutFile ([IO.Path]::GetTempPath()+'Quassel-Setup.exe'); Start-Process ([IO.Path]::GetTempPath()+'Quassel-Setup.exe')"
```

By default the wizard downloads just the matching speech engine and one model
for your hardware (lean and quick) — when it finishes, dictation works right
away. Tick **"Download everything now for full offline use"** in the installer
to fetch all engines and all five models (~4.3 GB) instead. If you download the
installer with a browser, SmartScreen may warn because it is not code-signed
yet — click "More info" → "Run anyway".

**No internet on the target PC?** Use the offline all-in-one package
(`Quassel-Offline-Windows.exe` plus its `.7z` parts) from the
[releases page](https://github.com/Skryx-L-A/quassel/releases/latest) — it
already contains the app, every speech engine and all five models (~4.3 GB).
Keep all parts in one folder, double-click the `.exe` to extract, then run
`Quassel.exe`. Nothing is ever downloaded.

> **Status:** the Windows build passed full hands-on testing (dictation,
> hands-free mode, settings, autostart) on Windows 11 with an NVIDIA GPU —
> but it is still young and unsigned. If anything misbehaves, I'd love your
> feedback — please [open an issue](https://github.com/Skryx-L-A/quassel/issues)
> and include any error message or exit code you see (also check
> `%LOCALAPPDATA%\Quassel\crash.log` and `debug.log`).

### Linux — Ubuntu, Debian, Mint, Fedora, Nobara, Arch, openSUSE

**Easiest — [download from quassel-voice.netlify.app](https://quassel-voice.netlify.app):**
one click, it picks the right file for your system.

Or one command for all supported distros (the installer detects your package
manager):

```bash
git clone https://github.com/Skryx-L-A/quassel.git && cd quassel && ./install.sh
```

It installs distro packages, builds whisper.cpp (NVIDIA/CUDA and AMD/Intel/
Vulkan auto-detected), downloads a speech model and sets up the app. Log out
and back in once if the script says so. Details: [INSTALL.md](INSTALL.md).

**No internet on the target Linux PC?** Use the offline all-in-one package
(`quassel-linux-x86_64.tar.gz.part-*`) from the
[releases page](https://github.com/Skryx-L-A/quassel/releases/latest): download
all parts into one folder, then

```bash
cat quassel-linux-x86_64.tar.gz.part-* | tar xzf -
cd quassel-linux-x86_64 && ./install.sh
```

It bundles a portable Python + Qt, both the CPU **and** CUDA engines (CUDA
runtime included; the NVIDIA driver is used from your system) and **all** speech
models — nothing is ever downloaded. Runs on x86_64 with glibc ≥ 2.28.

> **Tested:** Fedora / Nobara (daily driver). **Untested but prepared:**
> Ubuntu/Debian/Mint, Arch and openSUSE — the package lists are verified in
> containers, but no full install has run on real systems yet. Feedback is
> very welcome: please [open an issue](https://github.com/Skryx-L-A/quassel/issues)
> with the exact error output if something fails.

## Use

1. Open **Quassel** from your app launcher and turn it **on**.
2. Click into any text field:
   - **Hold** `Ctrl+Meta` → speak → **release** → text appears
   - **Double-tap** `Ctrl+Meta` → speak hands-free → **press once** → text appears
3. Everything else lives in the control center (click the pill or open the app).

Tips: a single short tap cancels; any other key while holding cancels (your
normal shortcuts keep working); say "scratch that" / „lösch das" to delete the
last dictation; turning Quassel off frees the GPU memory — handy before gaming.

## Uninstall

```bash
./uninstall.sh          # remove the app
./uninstall.sh --purge  # also remove whisper.cpp, models, venv, settings
```

## License

MIT — see [LICENSE](LICENSE). Uses [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
(MIT) and OpenAI's Whisper models (downloaded at install time).
