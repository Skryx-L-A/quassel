# Architecture

Quassel is a Python program that turns a held key combination into text at the
cursor, using a speech recognition server that runs on the same computer. This
page describes how the parts fit together. File names refer to the `quassel/`
package.

## The dictation core

The sequence of a dictation is written once, in `dictation.py`, and shared by
all three platforms. A dictation always runs like this:

1. **Hotkey.** A platform-specific listener reports key presses. All three feed
   the same state machine (`win/machine.py`, `ChordMachine`): hold the chord to
   talk and release to finish, double-tap for hands-free mode, press another key
   while holding to cancel.
2. **Start.** `DictationCore.start_recording()` plays the start tone, lowers
   other audio if configured, and starts the recorder. Recording is raw PCM
   (16 kHz, mono, 16-bit) into a file in the runtime folder, so partial
   transcripts can be cut from it while recording goes on.
3. **Stop.** On release the core keeps recording for a short tail (250 ms,
   450 ms on Bluetooth) so the last word is not clipped, stops the recorder,
   trims the start tone from the front and rejects recordings under about a
   quarter of a second.
4. **Transcribe.** The audio is wrapped as WAV and sent to the local speech
   server. If the server is not up, Quassel starts it and waits (30 seconds
   when it ran before, ten minutes for a cold start that has to load the
   model). If the server cannot be reached or the transcription fails, the
   recording is saved as `rescued-<timestamp>.wav` in the runtime folder (the
   five newest are kept) and the error names the file.
5. **Post-process** (`textproc.py`, `textreplace.py`, `progmode.py`, `itn.py`,
   `cleanup.py`, `aimodes.py`). In order: drop known silence hallucinations,
   spoken punctuation and voice commands, the programmer mode, text
   replacements, then either the opt-in clean-up or the opt-in AI mode. A
   per-app profile (`profiles.py`) can change several of these for the one
   dictation that goes into that application.
6. **Insert once.** The text goes to the target window through the clipboard
   and a paste keystroke, so every keyboard layout works. The previous
   clipboard content is saved first and restored afterwards, but only if the
   clipboard still holds Quassel's own text. Slow steps (clean-up, AI) finish
   before this insertion; Quassel never inserts text and then replaces it.
7. **After.** History (`config.py`), local statistics (`stats.py`) and
   auto-learned dictionary words are updated according to their switches. The
   text shown in the pill is removed from `state.json` after four seconds.

Voice commands such as "scratch that" do not insert text. They send Backspace
for the previous insertion, or press Enter (refused where a profile forbids it).

### Optional parts around the core

- **Live preview.** `PartialLoop` transcribes the last 15 seconds of audio at
  most every two seconds and shows the result in the pill. In hold mode it
  starts only after four seconds, because the server handles one request at a
  time and an early preview would delay the final transcript.
- **Streaming** (`streaming.py`). In hands-free mode and when enabled, the
  confirmed words of the preview are typed into the target window as you speak.
- **Auto-stop** (`vad.py`). In hands-free mode and when enabled, an `AutoStop`
  reads the growing recording, asks a voice-activity classifier whether each
  30 to 32 ms frame is speech, and ends the dictation after a pause, but only
  after speech was heard.
- **Wake word** (`wakeword.py`). A `WakeListener` thread with its own recorder
  waits for an utterance, optionally gated by a keyword spotter, transcribes it
  and checks for the wake phrase by sound. An insertion gate keeps a wake
  insertion and a keyboard dictation from writing at the same time. Audio and
  transcript text of this path are never logged and its scratch files are
  deleted after use.
- **Pre-roll** (`audio.py`). When enabled, the input stays open for three
  seconds after a dictation and keeps a ring buffer of the last moments, so the
  next dictation starts with audio from before the key press.

## Speech backends

`backends/base.py` defines the contract (`SpeechBackend`: `transcribe`,
`health`, `warmup`, plus capability flags for prompt, language hint, streaming
and timestamps). The only working implementation is
`backends/whisper_server.py`, an adapter over `whisperclient.py`, which talks
HTTP to the local `whisper-server` of whisper.cpp:

- It listens on `127.0.0.1`, port 8765 (a named profile gets its own port, see
  [CONFIG.md](CONFIG.md)), and Quassel adds a random URL prefix where the
  platform generates one (`REQUEST_PATH`).
- Requests bypass system proxy settings. A reply counts as "the server is up"
  only if its `Server` header starts with `whisper.cpp`, so a foreign program
  on the port is not mistaken for it.
- The audio window is capped for dictations under twelve seconds
  (`audio_ctx=768`) and decoding is greedy (`-bs 1`). Both are measured values,
  see `docs/measurements/`.
- The personal dictionary goes in as the prompt, within a token budget
  (`dictprompt.py`) because whisper.cpp keeps only the last 224 prompt tokens.
- `selfheal.py` runs after a failed transcription: it sends a tiny silent clip
  as a real inference request. If the server does not answer, it is restarted
  once and the transcription is retried once. The restart exists on macOS only,
  where the server is a child process of the app.

`backends/transcribe_cpp.py` is a stub for a possible second engine and is
inactive without an explicit binary path.

## Platform layers

| | Linux | macOS | Windows |
|---|---|---|---|
| Entry point | `quasseld` (`quassel.daemon`) as a systemd user service; control center `quassel-type` (`quassel.center`) | `quassel/mac_app.py`, one app bundle | `quassel/win/app.py`, one process |
| Hotkey | evdev (`/dev/input`), via the `input` group, no root | `CGEventTap` (`hotkey_mac.py`); needs Input Monitoring | Raw Input (`win/hook.py`) |
| Recording | `pw-record` or `parecord` subprocess | `sounddevice` stream (CoreAudio) in the daemon process | `sounddevice` (WASAPI) |
| Insertion | `wl-copy` or `xclip`, then `ydotool` sends Shift+Insert | clipboard plus Cmd+V through Quartz; needs Accessibility | Win32 clipboard plus Ctrl+V through `SendInput` |
| Speech server | `quassel-server.service` | child process, Metal build (`server_mac.py`) | child process `whisper-server.exe` (`win/server.py`) |
| Pill | GTK4 layer-shell pill (`pill.py`) as `quassel-pill.service`, or the Qt pill (`pill_qt.py`) | Qt pill plus menu-bar icon (`traymac.py`) | Qt pill plus tray icon, inside the app |
| Active-app lookup (profiles) | `xprop` on X11, `hyprctl` or `swaymsg` on Wayland | NSWorkspace | Win32 foreground window |
| Logs | systemd journal | `~/Library/Logs/Quassel/daemon.log` | `%LOCALAPPDATA%\Quassel\debug.log`, `crash.log` |

The pieces that differ per platform are small: recording, insertion, ducking,
notification and the hotkey source. `DictationCore` exposes them as a short
list of methods (`_paste`, `_type_chunk`, `_notify`, `_new_recorder`, ...) that
the Linux/macOS `Daemon` and the Windows `WinDictation` implement.

On macOS the app starts the speech server and the dictation daemon as child
processes and supervises the daemon. If a CoreAudio device locks up, the daemon
finishes the current dictation and exits with a special code; the app restarts
it, at most five times in five minutes, then switches dictation off and points
to the log. The Linux daemon is restarted by systemd.

## Data flow between processes

The dictation process never draws anything. It writes its state to
`state.json` in the runtime folder (atomically, replacing the file) with the
state (`idle`, `recording`, `transcribing`, `done`, `error`), a short text and,
while recording, the microphone level at up to 15 updates per second. The pill
polls that file. The control center changes settings by writing `config.ini`,
which the daemon notices by its modification time and reloads.

```
 keyboard --> hotkey listener --> ChordMachine --+
                                                 v
 microphone --> Recorder --> raw PCM file --> DictationCore --> WAV --> whisper-server
                                  |               |                       (127.0.0.1)
                                  |               +--> post-process <-- text
                                  |               |       (+ optional local AI endpoint)
                                  |               +--> clipboard + paste key --> target window
                                  |               +--> history.jsonl, stats.json
                                  +--> level --> state.json --> pill
```

## Threads

| Thread | Platform | Job |
|---|---|---|
| Event thread | all | Drives the chord state machine, starts and stops recordings and runs the synchronous part of each action. Because it does both, a recording always ends before the next one starts, without a lock. Linux: the evdev read loop. macOS: `mac-hotkey-worker`. Windows: `dictation-events` (`ChordPump`). |
| Tap thread | macOS | Receives key events from the system and only queues them, because macOS disables a tap whose callback is slow. |
| Hook thread | Windows | Reads Raw Input and queues key events. |
| Action thread | all | Runs the slow rest of a dictation: server wait, transcription, post-processing, insertion. `ActionRunner` can abandon a hung action and continue on a fresh thread; that is what the emergency stop uses. |
| Partial loop | all | Live-preview transcripts during recording. |
| Wake listener | all, when enabled | Listens for the wake word with its own recorder. |
| Recorder threads | all | Reader and writer for the audio stream or pipe, plus a level pump for the pill. |
| Watchdog | macOS daemon | Reports a stalled event thread once, with stack traces of all threads, and restarts the daemon if the event thread is dead. |
| Qt main thread | macOS, Windows, control center, Qt pill | Draws only; states arrive by signal or by polling `state.json`. |

Every long wait has a deadline. Emergency stop: the menu entry "Stop dictation
now", or `kill -USR2 <daemon pid>` on Linux and macOS. On macOS `kill -USR1`
writes the stacks of all threads to the log.

## Files and modules by role

| Role | Modules |
|---|---|
| Core flow | `dictation.py`, `win/machine.py`, `state.py` |
| Settings | `config.py`, `profile.py` (the instance switch), `profiles.py` (per-app), `fsutil.py` (atomic writes) |
| Speech | `whisperclient.py`, `backends/`, `dictprompt.py`, `selfheal.py`, `server_mac.py`, `win/server.py`, `hwdetect.py` |
| Text | `textproc.py`, `textreplace.py`, `progmode.py`, `itn.py`, `learn.py`, `cleanup.py`, `ai.py`, `aimodes.py` |
| Audio | `audio.py`, `win/audio_win.py`, `coreaudio.py`, `beep.py`, `mediacontrol.py`, `win/audioctl.py` |
| Detection | `vad.py`, `vad_silero.py`, `wakeword.py` |
| Platform glue | `daemon.py`, `platform_linux.py`, `platform_mac.py`, `hotkey_mac.py`, `win/` |
| Interface | `center.py`, `pill.py`, `pill_qt.py`, `traymac.py`, `i18n.py` |
| Tools | `cli.py`, `mcp_server.py`, `transcribe_file.py`, `stats.py`, `updatecheck.py`, `power.py`, `appctx.py`, `net.py` |
