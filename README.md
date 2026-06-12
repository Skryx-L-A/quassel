# Diktat — system-wide voice typing for Linux 🎤⌨️

**Hold `Ctrl+Meta` (Ctrl+Windows key), speak, release — your words are typed
wherever your cursor is.** Works in every app, including terminals. 100 %
offline and private: speech recognition runs locally on your machine with
[whisper.cpp](https://github.com/ggml-org/whisper.cpp), nothing is ever sent
to the cloud.

*Push-to-talk dictation / speech-to-text / voice keyboard for Linux — Wayland
and X11, KDE, GNOME and others.*

## Features

- **Push-to-talk:** hold `Ctrl+Meta`, talk, release → text is pasted at the cursor
- **Hands-free mode:** double-tap `Ctrl+Meta`, speak freely, press once → text is pasted
- **Works everywhere:** any GUI app *and* terminal windows (Konsole, GNOME Terminal, …)
- **Always uses your current default microphone** — change it in sound settings, dictation follows
- **Automatic language detection** (German, English and ~100 more via Whisper)
- **Keyboard-layout safe:** pastes via clipboard, so QWERTZ/AZERTY umlauts etc. are never mangled
- **GPU accelerated** on NVIDIA (CUDA) — sub-second transcription; CPU works too
- **Fully local & private** — no cloud, no accounts, no telemetry
- **Simple on/off app** ("Diktat") in your app launcher; no autostart unless you want it

## Why a daemon instead of a normal keyboard shortcut?

No desktop environment can bind a **modifier-only** combo like `Ctrl+Meta`.
Diktat ships a tiny Python daemon that watches the keyboard at the evdev
level (no root, via the `input` group), detects hold/double-tap of the chord
and drives the pipeline: record (`pw-record`) → transcribe (local
`whisper-server`) → paste (clipboard + `ydotool` Shift+Insert).

## Requirements

- Linux with **systemd** (Fedora/Nobara, Ubuntu/Debian, Arch, openSUSE, …)
- **PipeWire** (or PulseAudio) and **Wayland or X11**
- A microphone 🙂
- Optional: NVIDIA GPU with CUDA toolkit installed → much faster + better model by default

## Install

```bash
git clone https://github.com/Skryx-L-A/diktat-linux.git
cd diktat-linux
./install.sh
```

That's it — **one script installs everything**: distro packages, permissions,
whisper.cpp (built from source, CUDA auto-detected), a speech model, the
daemon, and the "Diktat" launcher app. It asks for `sudo` only where needed
(packages + permissions). If the script says so, **log out and back in once**
(group permission).

Model choice is automatic (GPU → `large-v3-turbo`, CPU → `small`), or pick one:

```bash
./install.sh --model base        # tiny | base | small | medium | large-v3-turbo
```

## Use

1. Open the **„Diktat"** app from your launcher and switch it **ON**
   (closing the window does not stop dictation — it's just a remote control).
2. Click into any text field, then:
   - **Hold** `Ctrl+Meta`, speak, **release** → text appears
   - **Double-tap** `Ctrl+Meta`, speak hands-free, **press once** → text appears

Notes: a single short tap cancels; pressing any other key while holding the
chord cancels (so shortcuts like `Ctrl+Meta+Arrow` still work); hands-free
recording auto-finishes after 5 minutes. The very first transcription loads
the model (a few seconds), afterwards it's fast. Turning Diktat OFF also
frees the GPU/RAM the model uses — handy before gaming.

## Troubleshooting

| Problem | Fix |
|---|---|
| Nothing happens on `Ctrl+Meta` | Log out/in once after install (group `input`). Check `systemctl --user status dictate-daemon` |
| Text isn't pasted | `journalctl --user -u dictate-daemon -f` while dictating shows what was recognized |
| "Whisper-Server nicht erreichbar" | `systemctl --user status whisper-server`; first start downloads nothing but loads the model — give it a few seconds |
| Wrong words / poor accuracy | Install a bigger model: `./install.sh --model large-v3-turbo` |
| Force one language | Edit `~/.config/systemd/user/whisper-server.service`: replace `-l auto` with e.g. `-l de`, then `systemctl --user daemon-reload && systemctl --user restart whisper-server` |

## Uninstall

```bash
./uninstall.sh          # remove app + services
./uninstall.sh --purge  # also remove whisper.cpp build + downloaded models
```

---

# Deutsch 🇩🇪

**`Strg+Meta` (Strg+Windows-Taste) halten, sprechen, loslassen — der Text
erscheint dort, wo der Cursor ist.** Funktioniert in jedem Programm, auch im
Terminal. Komplett offline: Die Spracherkennung (Whisper) läuft lokal auf
deinem PC, nichts geht in die Cloud.

## Installation

```bash
git clone https://github.com/Skryx-L-A/diktat-linux.git
cd diktat-linux
./install.sh
```

Ein einziges Skript installiert alles (Pakete, Berechtigungen, whisper.cpp,
Sprachmodell, Daemon, Startmenü-App). Falls das Skript es sagt: einmal ab-
und wieder anmelden.

## Benutzung

1. App **„Diktat"** im Startmenü öffnen und einschalten.
2. In ein Textfeld klicken, dann:
   - `Strg+Meta` **halten** → sprechen → loslassen → Text erscheint
   - `Strg+Meta` **2× tippen** → freihändig sprechen → 1× drücken → Text erscheint

Sprache (Deutsch/Englisch/…) wird automatisch erkannt. Das Diktat-Fenster ist
nur eine Fernbedienung — Schließen beendet das Diktat nicht. Ausschalten gibt
den GPU-Speicher des Modells frei (praktisch vorm Zocken).

## Lizenz / License

MIT — see [LICENSE](LICENSE). Uses [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
(MIT) and OpenAI's Whisper models (downloaded at install time).
