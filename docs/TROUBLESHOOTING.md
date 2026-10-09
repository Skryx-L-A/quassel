# Troubleshooting

Find the message you see (the pill, a notification or the control center show
these exact English texts; the German app shows the German wording from
`quassel/i18n.py`) and follow the steps. If nothing here helps, open an issue
and include the version and the log (see [Where to find the logs](#where-to-find-the-logs)).

## Where to find the logs

| Platform | Dictation log | Speech server log |
|---|---|---|
| Linux | `journalctl --user -u quasseld -n 50` (also `-u quassel-pill`, `-u quassel-ydotoold`) | `journalctl --user -u quassel-server -n 50` |
| macOS | `~/Library/Logs/Quassel/daemon.log` (older ones as `.1`, `.2`) | `~/Library/Application Support/Quassel/whisper-server.log` |
| Windows | `%LOCALAPPDATA%\Quassel\debug.log` and `crash.log` | `%LOCALAPPDATA%\Quassel\whisper-server.log` |

The daemon notes its version and start time in its log whenever it starts. The
Linux services are checked with `systemctl --user status quasseld quassel-server quassel-ydotoold`.
The logs do not contain dictated text.

## Nothing happens when I press the hotkey

- **Linux.** The daemon reads the keyboard through the `input` group. If the
  log says `FEHLER: Keine /dev/input-Geräte lesbar. Ist der Benutzer in der
  Gruppe 'input'?`, the group is missing or not active yet: log out and in
  again once. Check with `id -nG | tr ' ' '\n' | grep input`. Also make sure
  Quassel is switched on in the control center, and that `quasseld` runs:
  `systemctl --user status quasseld`.
- **macOS.** Quassel needs two permissions: *Input Monitoring* to notice the
  hotkey and *Accessibility* to insert text. The pill or a notification says
  "Quassel needs the “Input Monitoring” permission to notice the dictation
  hotkey (...)" or "Quassel needs the “Accessibility” permission to insert
  dictated text." Allow Quassel under System Settings → Privacy & Security,
  then restart Quassel.
- **macOS, “Secure input is on”.** The pill says "Secure input is on (a
  password field, or Secure Keyboard Entry in Terminal). Quassel can't hear the
  hotkey until it ends." and the menu-bar status line says "Secure input is on,
  hotkey paused". macOS hides every key from other programs while a password
  field has focus or Terminal's *Secure Keyboard Entry* is on. Leave the field,
  or turn Secure Keyboard Entry off.
- **Windows.** Check `%LOCALAPPDATA%\Quassel\crash.log` and `debug.log`.
- **Another program uses the same combination.** Choose another hotkey in the
  control center (`Ctrl + Meta`, `Alt + Meta` or `Ctrl + Alt`).

## Messages in the pill

| Message | What it means and what to do |
|---|---|
| `Canceled (single tap — double-tap or hold)` | You released before 0.5 seconds. Hold the keys longer, or double-tap for hands-free mode. Change the limit with `hold_min` in [CONFIG.md](CONFIG.md). |
| `Canceled (other key pressed)` | You pressed another key while holding the combination. That is the cancel gesture; your normal shortcuts keep working. |
| `Recording too short` | Less than about a quarter of a second of audio arrived. Hold the keys while you talk. |
| `Didn't catch that` | Nothing usable was recognised. Typical causes: silence, a muted or wrong microphone, or a recording that held only a known silence phrase such as "Thank you for watching". Check the microphone test (below). |
| `No sound from the microphone. Check mute, the selected input and microphone access.` | The pill shows a flat amber line while recording when nothing arrives from the microphone for 1.5 seconds. Check mute, the selected input in the control center, and the microphone permission. |
| `No microphone — is it on and connected?` | The recorder could not start. Connect or switch on a microphone, or choose another input. On Linux `pw-record` or `parecord` must exist (the notification then says `Fehler: pw-record/parecord fehlt`). On macOS the notification `Fehler: Mikrofonzugriff fehlt` means no usable input device or no microphone permission. |
| `The microphone stopped delivering sound during the recording. It may have been unplugged or disconnected.` | The input went away mid-dictation (unplugged, Bluetooth dropped, driver stopped the stream) before enough audio arrived. Reconnect it or pick another input. |
| `The microphone stopped during the recording. Only the part before that was transcribed.` | Same cause, but enough audio arrived before the drop: the first part was inserted, the rest is missing. |
| `The audio driver stopped responding. Restart Quassel to record again.` | Windows: stopping a recording hung inside the audio driver. Quassel refuses new recordings instead of writing half files until it restarts. |
| `Listening…` | After the wake phrase on its own, Quassel records what you say next. A tone plays only when signal tones are on. |
| `Whisper server unreachable` | The speech server did not answer. If Quassel just started or you switched to a big model, it may still be loading: try again in a moment. The recording is not lost; the message ends with `recording saved: <path>` and the file is a normal WAV you can transcribe later with the command-line client (`transcribe`, see [cli.md](cli.md)). If it never starts, read the speech server log (table above). |
| `Dictation could not be inserted` | The text could neither be pasted nor left on the clipboard, for example because the clipboard held an image or file that Quassel will not overwrite. The recording is saved (the message names the file) and the text is in the history when the history is on. |
| `Text is on the clipboard – press Ctrl+V` | The text was recognised but the paste key did not arrive. Quassel shows this only when the clipboard really holds your text. On Linux this means `ydotoold` is not running: `systemctl --user status quassel-ydotoold`. Press Ctrl+V yourself meanwhile. |
| `The text couldn't be inserted. On macOS, allow Quassel in System Settings → Privacy & Security → Accessibility. The text is on the clipboard.` | macOS dropped the paste key. Grant *Accessibility* and restart Quassel. |
| `Enter is blocked in this app` | A per-app profile with `allow_enter = off` (the shipped terminal profile) refused "press enter". This is the terminal guard working; edit `profiles.ini` to change it. |
| `Dictation stopped` | You used "Stop dictation now" (menu entry, or `kill -USR2 <daemon pid>`). Nothing is inserted after an emergency stop. |
| `No dictation is running` | You used the emergency stop while nothing was running. |
| `Audio device got stuck — restarting the dictation service` | macOS: a CoreAudio device locked up inside the system. Quassel finishes the dictation you just spoke and restarts its dictation service; the hotkey works again a moment later. |
| `The dictation service keeps restarting. Please check the log: <path>` | The restart happened more than five times in five minutes, so Quassel turned dictation off instead of restarting in circles. Read the daemon log. Switch the input in the control center and turn Quassel on again. |

## Control center messages

| Message | What it means and what to do |
|---|---|
| `The speech server isn't answering. If Quassel just started, it may still be loading the model. Try again in a moment.` | Shown by the microphone test and the file transcription when the server is down. Wait, or check the speech server log. |
| `The speech model is missing (<name>). Choose a model under Speech recognition to download it.` | The file named in `MODEL_PATH` in `server.env` does not exist. Pick a model in the control center, or run `model use <name> --download` of the command-line client ([cli.md](cli.md)). |
| `The speech server took too long to answer. A smaller model is faster.` | Choose a smaller or quantised model (`small-q5_1`, `base-q5_1`) on a CPU-only machine. |
| `The speech server couldn't transcribe this recording.` | The server answered with an error. Read the server log; try another model. |
| `Quassel isn't allowed to use the microphone. Allow it in System Settings → Privacy & Security → Microphone.` | macOS microphone permission was denied. |
| `The microphone couldn't be opened. Another app may be using it, or it was unplugged.` | Close other programs that hold the microphone, replug it, press "Refresh microphone list". |
| `The recording program is missing: <tool>.` | Linux: install the PipeWire tools (`pw-record`) or PulseAudio's `parecord`. |
| `No audio captured — check your microphone!` | The 2.5 second microphone test recorded nothing. |
| `Download failed: <reason>` | A model download did not complete or its checksum did not match; the partial file is discarded. Check the connection and retry. |
| `ffmpeg is required to read this file type.` | "Transcribe file" accepts 16-bit WAV without extra software; every other format needs `ffmpeg` on your `PATH`. Install it (`brew install ffmpeg`, your package manager, or from ffmpeg.org on Windows). |
| `Couldn't transcribe that file.` | The format is unsupported, the speech server is down, or the file could not be read. |
| `Couldn't check right now.` | The optional update check could not reach GitHub. |
| `Ollama not reachable at this address.` / `No response — is the model installed and Ollama running?` | The AI step cannot reach its endpoint. Check `[ai] endpoint` and that a model server is running. While it is unreachable, dictations are inserted unchanged. |
| `No models found — install one, e.g. ollama pull llama3.2:3b.` | The endpoint answered but lists no model. |

## Common problems

**The first dictation after switching on is slow.** The speech server loads the
model into memory on its first use: a few seconds for small models, up to
several minutes for large models on weak hardware. Later dictations are fast. A GPU helps a
lot (NVIDIA with CUDA, or any Vulkan GPU on Linux and Windows; Metal on macOS).

**Recognition is poor.** Pick a bigger model in the control center. Add names
and jargon to the personal dictionary. Set the spoken language instead of
`auto` if you always speak one language; use `mixed` for German with English
terms.

**Phrases like "Thank you for watching" appear that you never said.** Whisper
models invent text on silence. Quassel discards a short list of known phrases:
always for the unmistakable ones ("Thank you for watching", "Untertitel der
Amara.org-Community"), and for "Thank you" and "Vielen Dank" only when the clip
had no loud frames. Use a closer microphone, and make sure the noise
suppression of a headset is not muting you.

**Dictation works but typing in a terminal adds a newline or runs a command.**
Use a per-app profile with `allow_enter = off`
(`profiles init` of the command-line client, see [cli.md](cli.md), writes ready-made ones for terminals). With it a
trailing line break is removed and "press enter" is refused.

**Text lands in the wrong window or with wrong characters (Linux).** Quassel
pastes through the clipboard (`wl-copy` or `xclip`) and a Shift+Insert key sent
by `ydotool`. Install `wl-clipboard` (Wayland) or `xclip` (X11) and check that
`quassel-ydotoold` is running.

**A dictation seems stuck: the pill keeps recording and the hotkey does
nothing.** Use "Stop dictation now" from the menu or tray icon. Without a
menu: `kill -USR2 <daemon pid>` (Linux: `quassel-ctl panic`, which does the
same through systemd). A dictation stopped this way stays stopped. For a bug
report on macOS, `kill -USR1 <daemon pid>` writes the stacks of all threads to
the daemon log.

**The wake word does not trigger or triggers by mistake.** It is a beta
feature. Whisper has to hear the phrase correctly, and short odd phrases are
hard. Pick a phrase that is easy to pronounce, or train a keyword model and set
`spotter_model` (see [CONFIG.md](CONFIG.md)).

**Auto-stop never ends the dictation.** It needs `[vad] autostop = true`, a
double-tap start (hands-free mode), and then a pause longer than
`autostop_silence` after you have spoken. Constant background noise keeps the
loudness-based detector from seeing a pause; use the Silero model (see
[CONFIG.md](CONFIG.md)).

**A per-app profile does not apply.** Run `profiles list` of the command-line client ([cli.md](cli.md)): it shows
what was parsed. Matching uses the application's bundle id, process name or
window class, case-insensitive. On GNOME and KDE under Wayland the active
application is not available, so no profile can match; use an X11 session or
Hyprland or Sway.

**Two instances (`QUASSEL_PROFILE`).** A named
profile uses its own folders and its own port; see [CONFIG.md](CONFIG.md). The
installed app is unaffected.

**Settings were reset, and there is a file `config.ini.damaged-…`.** The
settings file could not be read completely (for example after a manual edit).
Before saving a change, Quassel kept the whole damaged file next to it; copy the
lost settings back from there. Quassel never overwrites a damaged file silently.

**Quassel refuses to start with "QUASSEL_PROFILE=… is invalid".** Profile names
may only contain a-z, 0-9 and "-", at most 32 characters. A profile also may not
use port 8765, which belongs to the installed Quassel.

## Still stuck

Open an issue at <https://github.com/Skryx-L-A/quassel/issues> and fill in the
template: version, platform, the log lines around the problem and the steps to
reproduce. Do not paste dictated text or recordings you want to keep private.

The quickest way to collect most of this: open the control center, go to
**About** and press **Copy diagnostics** (German: **Diagnose kopieren**), then
paste the report into the issue. **Save as file…** writes the same report to a
file that only you can read. Without the control center, run
`quassel-type diagnostics`. The report never contains your history,
dictionary, replacements, AI mode texts or anything you dictated; see
[PRIVACY.md](PRIVACY.md#the-diagnostics-report) for the full list. Read it
before you post it: it is plain text.
