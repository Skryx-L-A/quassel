# Installation guide / Installationsanleitung

## English

### 1. Requirements

- A Linux distribution with **systemd** — tested targets: Fedora / Nobara,
  Ubuntu / Debian, Arch / Manjaro, openSUSE
- **Wayland or X11** desktop (KDE, GNOME, etc.) and **PipeWire** or PulseAudio
- A working microphone
- ~2 GB free disk space (whisper.cpp build + speech model)
- Optional but recommended: **NVIDIA GPU with the CUDA toolkit** (`nvcc`)
  installed before running the installer → much faster transcription and a
  better default model

### 2. Download

Either clone with git:

```bash
git clone https://github.com/Skryx-L-A/diktat-linux.git
cd diktat-linux
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
skipped.

Optional: choose the speech model yourself (default is automatic —
`large-v3-turbo` with NVIDIA GPU, `small` on CPU):

```bash
./install.sh --model base    # tiny | base | small | medium | large-v3-turbo
```

| Model | Size | Quality | CPU-friendly? |
|---|---|---|---|
| tiny | 75 MB | low | yes |
| base | 142 MB | okay | yes |
| small | 466 MB | good | yes |
| medium | 1.5 GB | very good | slow |
| large-v3-turbo | 1.6 GB | best | needs GPU for speed |

### 4. Log out and back in (only if the script says so)

If the installer added you to the `input` group, the permission only becomes
active after you **log out and log in again** (or reboot) once.

### 5. Start dictating

1. Search for **“Diktat”** in your application launcher and open it.
2. Flip the switch to **ON**. (You can close the window — dictation keeps running.)
3. Click into any text field, then:
   - **Hold** `Ctrl+Meta` (Ctrl + Windows key) → speak → **release** → text appears
   - or **double-tap** `Ctrl+Meta` → speak hands-free → **press once** → text appears

The first transcription after switching ON takes a few seconds (model
loading); after that it is fast.

### Troubleshooting

- **Nothing happens at all** → did you log out/in after installing? Check
  `systemctl --user status dictate-daemon` and
  `journalctl --user -u dictate-daemon -n 20`.
- **Recording works but no text appears** → check
  `systemctl --user status whisper-server diktat-ydotoold`.
- **Bad recognition quality** → install a bigger model, e.g.
  `./install.sh --model large-v3-turbo`.
- **Force a single language** (instead of auto-detect): edit
  `~/.config/systemd/user/whisper-server.service`, change `-l auto` to
  `-l de` (or `en`, …), then
  `systemctl --user daemon-reload && systemctl --user restart whisper-server`.

### Uninstall

```bash
./uninstall.sh           # remove app + services
./uninstall.sh --purge   # also delete whisper.cpp + models (~2 GB)
```

---

## Deutsch

### 1. Voraussetzungen

- Linux mit **systemd** (Fedora/Nobara, Ubuntu/Debian, Arch, openSUSE)
- **Wayland oder X11** (KDE, GNOME, …) und **PipeWire** oder PulseAudio
- Funktionierendes Mikrofon, ~2 GB freier Speicherplatz
- Optional: **NVIDIA-GPU mit CUDA-Toolkit** (`nvcc`) → deutlich schneller

### 2. Herunterladen

```bash
git clone https://github.com/Skryx-L-A/diktat-linux.git
cd diktat-linux
```

…oder auf GitHub **Code → Download ZIP**, entpacken, Terminal im Ordner öffnen.

### 3. Installieren — ein Befehl

```bash
./install.sh
```

Das Skript erledigt alles (Pakete, Berechtigungen, whisper.cpp-Build,
Modell-Download, App). Es kann gefahrlos erneut ausgeführt werden.
Modellwahl optional: `./install.sh --model base` (siehe Tabelle oben).

### 4. Einmal ab- und anmelden

Nur nötig, wenn das Skript es am Ende sagt (Gruppen-Berechtigung).

### 5. Diktieren

1. App **„Diktat“** im Startmenü öffnen, Schalter auf **AN**.
2. In ein Textfeld klicken:
   - `Strg+Meta` **halten** → sprechen → loslassen → Text erscheint
   - `Strg+Meta` **2× tippen** → freihändig sprechen → 1× drücken → Text erscheint

Bei Problemen: siehe Troubleshooting oben (englischer Abschnitt) oder ein
GitHub-Issue aufmachen.
