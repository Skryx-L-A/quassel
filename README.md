<img src="assets/voxtype.svg" width="92" align="left" alt="VoxType logo">

# VoxType — local, private voice typing

**Hold `Ctrl+Meta` (Ctrl+Windows key), speak, release — your words are typed
wherever your cursor is.** A clean, minimal pill at the bottom of your screen
shows a live waveform and a live transcript while you speak — inspired by the
best commercial dictation apps, but **100 % offline and open source**: speech
recognition runs locally via [whisper.cpp](https://github.com/ggml-org/whisper.cpp).
No cloud, no account, no subscription, no telemetry.

**Linux** and **Windows** (Windows build is fresh — see
[windows/build.md](windows/build.md)). Fully offline, no cloud, no account.

<p align="center"><img src="assets/screenshots/demo.gif" alt="VoxType pill while dictating: pulsing red dot, live transcript, result" width="700"></p>

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
VoxType ships a small daemon that watches the keyboard at the evdev level
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

### Windows 10 / 11 (beta)

Paste this into **PowerShell** — it downloads the installer and opens the
setup wizard right away; afterwards VoxType is ready to use:

```powershell
irm https://github.com/Skryx-L-A/voxtype/releases/latest/download/VoxType-Setup.exe -OutFile "$env:TEMP\VoxType-Setup.exe"; & "$env:TEMP\VoxType-Setup.exe"
```

SmartScreen will warn because the installer is not code-signed yet — click
"More info" → "Run anyway". On first launch VoxType downloads whisper.cpp
and a speech model (CUDA build if you have an NVIDIA GPU).

> **Beta status:** the Windows build is young and tested on a single
> Windows 11 machine so far. If anything misbehaves, I'd love your
> feedback — please [open an issue](https://github.com/Skryx-L-A/voxtype/issues)
> and include any error message or exit code you see (also check
> `%LOCALAPPDATA%\VoxType\crash.log`).

### Linux — Ubuntu, Debian, Mint, Fedora, Nobara, Arch, openSUSE

One command for all supported distros (the installer detects your package
manager):

```bash
git clone https://github.com/Skryx-L-A/voxtype.git && cd voxtype && ./install.sh
```

It installs distro packages, builds whisper.cpp (NVIDIA/CUDA and AMD/Intel/
Vulkan auto-detected), downloads a speech model and sets up the app. Log out
and back in once if the script says so. Details: [INSTALL.md](INSTALL.md).

> **Tested:** Fedora / Nobara (daily driver). **Untested but prepared:**
> Ubuntu/Debian/Mint, Arch and openSUSE — the package lists are verified in
> containers, but no full install has run on real systems yet. Feedback is
> very welcome: please [open an issue](https://github.com/Skryx-L-A/voxtype/issues)
> with the exact error output if something fails.

## Use

1. Open **VoxType** from your app launcher and turn it **on**.
2. Click into any text field:
   - **Hold** `Ctrl+Meta` → speak → **release** → text appears
   - **Double-tap** `Ctrl+Meta` → speak hands-free → **press once** → text appears
3. Everything else lives in the control center (click the pill or open the app).

Tips: a single short tap cancels; any other key while holding cancels (your
normal shortcuts keep working); say "scratch that" / „lösch das" to delete the
last dictation; turning VoxType off frees the GPU memory — handy before gaming.

## Uninstall

```bash
./uninstall.sh          # remove the app
./uninstall.sh --purge  # also remove whisper.cpp, models, venv, settings
```

## License

MIT — see [LICENSE](LICENSE). Uses [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
(MIT) and OpenAI's Whisper models (downloaded at install time).
