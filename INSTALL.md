# Installation guide / Installationsanleitung

## English

### 1. Requirements

- A Linux distribution with **systemd** — tested targets: Fedora / Nobara,
  Ubuntu / Debian, Arch / Manjaro, openSUSE
- **Wayland or X11** desktop (KDE, GNOME, etc.) and **PipeWire** or PulseAudio
- A working microphone
- **Python 3.10 or newer**
- ~2 GB free disk space (whisper.cpp build + speech model)
- Optional but recommended: a GPU — **NVIDIA** (CUDA toolkit installed) or any **Vulkan-capable**
  AMD/Intel/NVIDIA card → much faster transcription and a better default model
- Optional: `ffmpeg`, only to transcribe audio or video files that are not 16-bit
  WAV (dictation itself does not need it)

### 2. Download

Either clone with git:

```bash
git clone https://github.com/Skryx-L-A/quassel.git
cd quassel
```

…or click **Code → Download ZIP** on GitHub, unpack it, and open a terminal
in the unpacked folder.

### 3. Install — one command

```bash
./install.sh
```

(If you downloaded the ZIP you may need `chmod +x install.sh` first.)

The script does everything: installs distro packages (asks for your `sudo`
password), sets up keyboard permissions, builds whisper.cpp, downloads a
speech model, and installs the app. It is safe to re-run; finished steps are
skipped. whisper.cpp is built at a fixed version (currently v1.9.5, the same
for everybody), not the newest source; the first build takes a few minutes.
When that version changes in a later Quassel release, the next run rebuilds it.

Instead of cloning, the small online installer from the
[releases page](https://github.com/Skryx-L-A/quassel/releases/latest)
(`quassel-install.sh`) fetches the latest release and runs the same script. If
that release carries a prebuilt engine package (`quassel-engine-linux-*.tar.gz`)
it is downloaded, checked against its checksum and used instead of building;
otherwise the installer says so and builds from source. `./install.sh --prebuilt`
does the same check from a clone. The big offline all-in-one package exists only
for the older v2.2.0.

Optional: choose the speech model yourself — also on an existing installation,
where it switches to that model (a model you picked earlier stays when you run
the script without `--model`). On Linux the automatic choice is `large-v3-turbo`
when a GPU is found, otherwise a quantized model that fits the CPU:
`small-q5_1` with four or more cores, `base-q5_1` on smaller CPUs. (The
macOS and Windows apps choose their own default from the same hardware check:
`large-v3-turbo-q5_0` on Apple Silicon with 16 GB of memory or more.)

```bash
./install.sh --model small-q5_1
```

| Model | Size | Quality | CPU-friendly? |
|---|---|---|---|
| tiny | 75 MB | low | yes |
| base | 142 MB | okay | yes |
| base-q5_1 | quantized base | good | yes |
| small | 466 MB | good | yes |
| small-q5_1 | quantized small | good | yes |
| medium | 1.5 GB | very good | slow |
| medium-q5_0 | quantized medium | very good | limited |
| large-v3-turbo | 1.6 GB | best | needs GPU for speed |
| large-v3-turbo-q5_0 | quantized turbo | best | limited |
| large-v3 | full-size model | best | no |

### 4. Log out and back in (only if the script says so)

If the installer added you to the `input` group, the permission only becomes
active after you **log out and log in again** (or reboot) once.

### 5. Start dictating

1. Search for **"Quassel"** in your application launcher and open it.
2. Flip the switch to **ON**. (You can close the window — dictation keeps running.)
3. Click into any text field, then:
   - **Hold** `Ctrl+Meta` (Ctrl + Windows key) → speak → **release** → text appears
   - or **double-tap** `Ctrl+Meta` → speak hands-free → **press once** → text appears

The first transcription after switching ON takes a few seconds (model
loading); after that it is fast.

### Optional features

Everything below is off until you switch it on; the control center covers the
common settings, [docs/CONFIG.md](docs/CONFIG.md) lists the rest.

- **Per-app profiles** (no Enter in terminals and so on): write `profiles.ini`
  with the ready-made examples using `profiles init` of the command-line client
  ([docs/cli.md](docs/cli.md)).
- **Dictation clean-up, hands-free auto-stop, pre-roll buffer, wake-word keyword
  model, neural voice detection:** keys in `~/.config/quassel/config.ini`. The
  last two need `pip install onnxruntime==1.22.0` into Quassel's own virtual
  environment (`~/.local/share/quassel/venv`) and a model file that you supply.
- **A second copy next to the installed one:** start it with
  `QUASSEL_PROFILE=next`; it then uses `~/.config/quassel-next`,
  `~/.local/share/quassel-next` and its own speech-server port.

### Troubleshooting

The full list of messages and fixes is in
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). The most common ones:

- **Nothing happens at all** → did you log out/in after installing? Check
  `systemctl --user status quasseld` and `journalctl --user -u quasseld -n 20`.
- **Recording works but no text appears** → check
  `systemctl --user status quassel-server quassel-ydotoold`.
- **Bad recognition quality** → pick a bigger model in the control center
  (it downloads automatically), and add tricky names to the personal dictionary.
- **Force a single language** → control center → "Spoken language".

What Quassel stores and which connections it can make: [docs/PRIVACY.md](docs/PRIVACY.md).

### Uninstall

```bash
./uninstall.sh           # remove app + services
./uninstall.sh --purge   # also delete whisper.cpp, models, venv, history and settings (~2 GB)
```

At the end the script lists the system permissions the installer added (the
udev rule for `/dev/uinput`, the module-load entry and your membership in the
`input` group) and asks whether to remove them too. The answer defaults to no,
because other programs may need the group.

---

## Deutsch

### 1. Voraussetzungen

- Linux mit **systemd** (Fedora/Nobara, Ubuntu/Debian, Arch, openSUSE)
- **Wayland oder X11** (KDE, GNOME, …) und **PipeWire** oder PulseAudio
- Funktionierendes Mikrofon, ~2 GB freier Speicherplatz
- **Python 3.10 oder neuer**
- Optional: **NVIDIA-GPU mit CUDA-Toolkit** (`nvcc`) → deutlich schneller

### 2. Herunterladen

```bash
git clone https://github.com/Skryx-L-A/quassel.git
cd quassel
```

…oder auf GitHub **Code → Download ZIP**, entpacken, Terminal im Ordner öffnen.

### 3. Installieren — ein Befehl

```bash
./install.sh
```

Das Skript erledigt alles (Pakete, Berechtigungen, whisper.cpp-Build,
Modell-Download, App). Es kann gefahrlos erneut ausgeführt werden.
whisper.cpp wird in einer festen Version gebaut (derzeit v1.9.5); der erste Bau
dauert einige Minuten. Gibt es zum Release vorgebaute Engine-Pakete, lädt der
Online-Installer sie nach einer Prüfsummenprüfung, sonst baut er selbst. Das
große Offline-Komplettpaket gibt es nur für die ältere Version v2.2.0.
Modellwahl optional, zum Beispiel: `./install.sh --model small-q5_1` — das gilt
auch für eine bestehende Installation.
Die vollständige Liste mit quantisierten q5-Modellen steht in der englischen
Tabelle oben.

### 4. Einmal ab- und anmelden

Nur nötig, wenn das Skript es am Ende sagt (Gruppen-Berechtigung).

### 5. Diktieren

1. App **„Quassel“** im Startmenü öffnen, Schalter auf **AN**.
2. In ein Textfeld klicken:
   - `Strg+Meta` **halten** → sprechen → loslassen → Text erscheint
   - `Strg+Meta` **2× tippen** → freihändig sprechen → 1× drücken → Text erscheint

Bei Problemen: siehe Troubleshooting oben (englischer Abschnitt) oder ein
GitHub-Issue aufmachen.

---

## macOS (Apple Silicon)

### 1. Requirements

- macOS on **Apple Silicon** (M1 or newer)
- A working microphone
- ~2 GB free disk space (whisper.cpp engine + speech model)
- No Apple Developer account needed — installs are unsigned / not notarized

### 2. Install — pick one

**Homebrew (recommended)** — installs the app and clears the quarantine flag for
you, so there is no Gatekeeper dialog:

```bash
brew install --cask skryx-l-a/quassel/quassel
```

**DMG** — download directly from the
[releases page](https://github.com/Skryx-L-A/quassel/releases/latest/download/Quassel-macOS-arm64.dmg)
(~44 MB). Because it is not notarized, macOS blocks it on first launch — open
**System Settings → Privacy & Security** and click **"Open Anyway"** next to the
Quassel warning, then confirm in the dialog that appears.

**Terminal — one line:**

```bash
curl -fsSL https://github.com/Skryx-L-A/quassel/releases/latest/download/quassel-install-macos.sh | bash
```

### 3. First launch — grant permissions

macOS asks once for **Microphone**, **Accessibility** and **Input Monitoring**.
Grant all three, then **restart Quassel** so the permissions take effect.

### 4. Start dictating

1. Open **Quassel** from the menu bar and turn it **on**.
2. Click into any text field, then:
   - **Hold** `Ctrl+Cmd` → speak → **release** → text appears
   - or **double-tap** `Ctrl+Cmd` → speak hands-free → **press once** → text appears

### Troubleshooting

- **Gatekeeper still blocks it** → System Settings → Privacy & Security → scroll
  down to the warning → "Open Anyway", then confirm.
- **Nothing happens when dictating** → check that Accessibility and Input
  Monitoring are both granted for Quassel, then restart the app.
- **Bad recognition quality** → pick a bigger model in the control center.

### Uninstall

Homebrew install: `brew uninstall --cask skryx-l-a/quassel/quassel`.
DMG or Terminal-installer install: drag `Quassel.app` out of `/Applications` to
the Trash. Settings, history and downloaded models stay in
`~/Library/Application Support/Quassel`, and logs in `~/Library/Logs/Quassel`;
delete those folders to remove everything.

---

## Windows 10 / 11

### 1. Requirements

- Windows 10 or 11, a working microphone, and disk space for the speech engine
  and one model (the setup downloads both; the model is chosen for your hardware)
- No administrator rights are needed (the setup installs for the current user)

### 2. Install

Download `Quassel-Setup.exe` from the
[releases page](https://github.com/Skryx-L-A/quassel/releases/latest) or the
website, and run it. The setup downloads the speech engine that matches your
hardware and one model, so dictation works as soon as it finishes. If a browser
downloaded the file, SmartScreen may warn because the installer is not
code-signed yet: click "More info", then "Run anyway". For a PC without internet
use the offline package of
[v2.2.0](https://github.com/Skryx-L-A/quassel/releases/tag/v2.2.0); to build the
installer yourself, see [windows/build.md](windows/build.md).

### 3. Start dictating

1. Start **Quassel** from the Start menu; it lives in the tray.
2. Click into any text field, then **hold** `Ctrl+Win`, speak, **release**, or
   **double-tap** `Ctrl+Win` for hands-free mode and press once to finish.

### Troubleshooting

Logs are in `%LOCALAPPDATA%\Quassel` (`debug.log`, `crash.log`,
`whisper-server.log`); see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

### Uninstall

Use Windows Settings → Apps. The uninstaller asks whether to also delete the
downloaded speech engine and models and your settings, dictionary and history.
