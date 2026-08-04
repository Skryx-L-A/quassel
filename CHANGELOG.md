# Changelog

All notable changes to Quassel are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Release-notes discipline (how to keep this file)
- Every user-facing change lands an entry under **[Unreleased]** in the same commit.
- Group entries: **Added / Changed / Fixed / Removed** (also Security/Deprecated when relevant).
- Write for users, not developers: what changed and why it matters, no internal jargon.
- On release: rename **[Unreleased]** to the version + date, copy its body into the GitHub
  release notes, then start a fresh empty **[Unreleased]** block. Tag = `vX.Y.Z`.
- Keep newest version on top.

## [Unreleased]

## [2.6.0] - 2026-08-04

> macOS and Linux are new in this release. The Windows installer
> (`Quassel-Setup.exe`) is carried over unchanged from 2.5.0 — it is built on
> Windows and none was available; the changes below reach Windows with the next
> build from source.

### Changed
- Dictation decodes with greedy search on every platform now. Beam search was the default
  wherever a GPU was found; measured over 36 recordings and 938 reference words it was never
  more accurate, on one noisy take worse, and about 6 % slower (median 0.595 s against
  0.557 s). An installation that still carries the old default is moved over on the next
  start. A decode setting you picked yourself stays untouched.
- Short dictations are transcribed faster: anything under twelve seconds runs with a smaller
  audio window in the speech engine. That cut the time in the speech server by 38 to 44 % at a
  word error rate identical to the full window — 0.484 s to 0.270 s on a 5.8-second recording,
  0.560 s to 0.349 s on a 12.6-second one. Longer recordings keep the full window: from about
  14 seconds the shorter one starts costing accuracy, and past 18 seconds the decoder runs into
  a repetition loop. Measured on Apple Silicon with `large-v3-turbo-q5_0` and confirmed
  afterwards on Linux/CUDA with `base-q5_1` and `small`, the models a machine without an NVIDIA
  card loads — they hold the threshold with more room than the large one. Windows is untested.
  Data and scripts are in `docs/measurements/`.

### Added
- macOS/Windows: **"Click on the pill opens the control center"** in the control center. It is
  on by default; switch it off when opening the settings window by accident gets in the way.
  The right click keeps toggling dictation, and dragging the pill is unaffected.

- macOS/Windows: the pill can now be dragged to any position on screen. Turn it on with
  **"Pill can be moved"** in the control center (off by default); a plain click still opens the
  control center, and a drag of at least a few pixels moves the pill instead. Turning the
  setting back off snaps the pill back to its usual spot at the bottom of the screen. A new
  **"Reset position"** button next to the setting snaps the pill back to that default spot
  right away, whether or not "Pill can be moved" is currently on.

- macOS: the menu-bar icon has a new entry **"Diktat sofort beenden"** that stops a running
  dictation at once — for the rare case where the hotkey no longer responds. The same
  emergency stop runs from a terminal with `kill -USR2 <daemon-pid>`.
- macOS: `kill -USR1 <daemon-pid>` writes the stacks of all daemon threads to
  `~/Library/Logs/Quassel/daemon.log`. Every line in that log now carries a timestamp, and
  the daemon notes its version and start time on launch, so a hang can be pinned down after
  the fact.

- macOS: the app supervises its own dictation service. Some audio devices deadlock inside
  macOS — recorded on a stuck daemon, with two threads waiting on each other's mutex — and no
  timeout can unstick that. Quassel now inserts the dictation you just spoke, restarts the
  service, and has it back within about two seconds; a notification says what happened. More
  than five restarts in five minutes switch dictation off instead, with a pointer to the log.
  Only that one restart request brings the service back — a daemon you stop yourself stays
  stopped.

### Fixed
- The speech-engine settings file is written atomically now. It used to be emptied first and
  refilled afterwards, so a crash or a power cut in that moment left it blank — and the next
  start would look for a model on its own instead of using the one you picked.
- macOS: the tone that marks the start of a recording often stayed silent over Bluetooth
  headphones, while the closing tone was reliable. An idle Bluetooth link swallows the first
  few hundred milliseconds of playback when it wakes up, and both tones are shorter than that.
  Quassel now plays them itself instead of handing them to `afplay`, over an output that keeps
  running for a minute after a tone so the link stays awake through a working session, and it
  slips a quarter second of silence in front of a tone whenever the link has to wake up first.
- macOS/Windows: moving the pill's opacity slider in the control center had no visible effect
  until the pill's mode changed next (e.g. starting a recording) — a config reload only resized
  the pill window, it never repainted it. The oval background and the live-preview bubble now
  update immediately when the slider moves; the waveform bars are unaffected by the setting and
  stay fully visible at every opacity level, as intended.
- macOS: dictation could wedge for good — the recording kept running and pressing the chord
  no longer ended it, until Quassel was restarted. A slow or faulty audio device blocked the
  thread that handles the keyboard. Stopping a recording, every AppleScript call and the wait
  for the speech server now have hard time limits, and that thread no longer does the work
  itself, so it stays responsive even while a dictation is being transcribed.
- macOS: starting a new dictation while the previous one was still being transcribed could
  throw away the finished text, stop the wrong recording, and leave the microphone running.
  Stopping a recording and reading it now happen on the same thread that starts one, so the
  two can no longer overtake each other; only transcription runs in the background.
  Recordings additionally alternate between two files on disk, so a new one can never
  overwrite the audio of the previous.
- A dictation is no longer lost when the speech server does not come up or the transcription
  fails: the recording is written to `rescued-<timestamp>.wav` in Quassel's runtime folder and
  the error message names the file. Two failures in the same second get separate files, and
  the five most recent rescues are kept. On a cold start,
  where the model still has to load, Quassel now waits for the server instead of giving up
  after 30 seconds. Applies to Linux, Windows and macOS.
- macOS: pressing "Diktat sofort beenden" during transcription no longer inserts the text
  anyway — not even when you start the next dictation right after, which used to take the
  stop back. With nothing running, the entry now says so instead of doing nothing silently.

## [2.5.0] - 2026-07-28

### Added
- **Native macOS support (Apple Silicon)**: menu-bar app with the familiar pill overlay
  (visible on every Space and over fullscreen apps), `Ctrl+Cmd` chord dictation
  (hold = push-to-talk, double-tap = hands-free), Metal-accelerated whisper.cpp,
  settings window with on/off toggle, login-item autostart, media auto-pause.
  Install via Homebrew (`brew install --cask skryx-l-a/quassel/quassel`), a DMG,
  or a one-line Terminal installer — not notarized yet (no Apple Developer
  account), so first launch needs one Gatekeeper approval. Build from source via
  `scripts/build_mac_app.sh` (see README) remains available too.
- Quantized Whisper models (q5) in the model picker (`base-q5_1`, `small-q5_1`, `medium-q5_0`,
  `large-v3-turbo-q5_0`) — mainly smaller downloads / less RAM (CPU speed gain is small).
- Voice Activity Detection (Silero VAD): skips silence and stops phantom text on silence
  (e.g. "Thank you" / "Untertitel der Amara.org-Community") — a reliability win, ~free.
- Start/stop beeps (toggle in settings, on by default): a rising tone when it starts listening
  and a falling tone when it stops — especially handy with headphones.
- Beam search on machines with a GPU (more accurate, sub-second there); CPU stays greedy.
- Full `large-v3` model option for maximum accuracy on strong GPUs (turbo stays the default).

### Fixed (accuracy / Bluetooth)
- Bluetooth headsets / earbuds (AirPods etc.): the start and end of sentences were getting
  clipped (the A2DP→HFP profile switch). Quassel now pads the tail and trims the noisy
  lead-in — more so when a Bluetooth mic is detected — so words aren't cut off.
- Decoding is now hardware-aware: beam search on GPU (accuracy), greedy + no temperature
  fallback on CPU (caps worst-case time). Spoken-language default stays auto-detect.

### Changed
- Default model without an NVIDIA GPU is now `small-q5_1` (≥4 cores) or `base-q5_1` — `medium`
  and larger are too slow for live dictation on CPU and are no longer auto-selected (still
  pickable in settings).
- whisper-server now uses a tuned thread count (up to 8 — measured ~1.45× faster) and
  `--no-fallback` (caps the worst-case decode time on hard/noisy audio).

### Fixed
- Performance on weaker hardware: the live-preview transcription that ran every 2 s during
  dictation is now skipped when the preview bubble is off and streaming is off — it was pure
  wasted CPU that competed with and slowed the final transcription.
- **macOS recording dropped about 11 % of the audio.** Recording went through
  `ffmpeg`/AVFoundation, which discarded material continuously (measured: 3128 of 3520
  periods of an 8-second reference tone; 2.3 s missing from a 20 s recording) and lost a
  further 0.73 s at the start of every take. Recording now runs in-process through
  sounddevice/CoreAudio: 100 % of the audio, correct pitch, 0.002 s start loss, and better
  anti-aliasing. As a side effect macOS no longer needs `ffmpeg` installed to dictate — it
  is only used to transcribe existing non-WAV audio files.

## [2.4.0] - 2026-06-14

> Online installer is v2.4.0. The offline all-in-one packages remain v2.2.0 for now
> (older) and are noted as such on the download page.

### Added
- Local AI tier (opt-in, off by default, 100% on your machine via Ollama):
  - Auto-clean every dictation (remove filler words, fix grammar/punctuation) — choose the mode.
  - Smart formatting modes: email, bullet list, tidy paragraphs, formal, concise.
  - Voice modes: start a dictation with "as an email", "as a list", "make it concise" (also in
    German) to reshape just that dictation.
  - Custom modes: define your own name=instruction prompts and trigger them by name.
  - New AI settings page: enable, Ollama address, model picker, auto mode, voice modes, a live Test.
  - Fails soft: if Ollama or the model isn't there, your dictation is inserted as plain text.
- First-run onboarding wizard: a short, skippable welcome that explains the hotkey and the
  "nothing to configure, just close it" idea, and points out the wake word can be changed.
- Hands-free wake word (opt-in, off by default): say a phrase (default "Hey Quassel") to
  start dictation, stop automatically after a short silence. The phrase is editable in
  settings — handy if the default is awkward to pronounce in your language.
- Pause media while dictating, now on Windows too (System Media Transport Controls),
  matching the existing Linux (MPRIS) behaviour.
- Reset to defaults: one click in settings restores every setting to its original value.
- In-app update check: Quassel can tell you when a newer release is available.
- Text replacement / snippet expansion: define shortcuts that expand as you dictate.
- Auto-add words to the personal dictionary.
- Programmer dictation mode: speak camelCase, snake_case and common symbols.
- Voice command "press Enter".
- Audio-file transcription: drop in an audio file and get the text.
- Local usage statistics: words dictated and time saved, computed and stored only on your machine.

- Usage statistics now have their own page with a visual bar chart: total words dictated, time
  spoken, and words dictated today (by your computer's local date — no internet needed).

### Changed
- Streaming typing now appears word by word as you speak, instead of in larger multi-word
  chunks — words may still be refined afterwards as the recognizer hears more context.
- Better handling of mixed-language dictation (e.g. German with English words in one sentence).
- "Check for updates on start" is now OFF by default.

### Removed
- (none)

### Fixed
- Local AI post-processing now reliably inserts the text: the recognized text is pasted
  immediately (while your cursor is still in the field) and then replaced with the AI-refined
  version once it's ready — so slower/larger models no longer leave the target window empty.
- The floating pill no longer turns Quassel off on a stray left-click. Left-click now opens the
  control center (as documented); on/off moved to right-click. Previously, clicking into a text
  field that sat under the pill — e.g. mid-dictation — could shut Quassel down entirely.

### Changed (settings layout)
- The Speech page now lists the Whisper model and microphone above the wake word; Beta features
  always sit at the very bottom.

### Beta
- Wake word (hands-free voice activation) is shipped as **Beta** — opt-in, off by default, and
  clearly labelled in settings. It is not reliable yet (see GitHub issue #33). Improvements so far:
  tolerant matching, phrase-biased recognition, a dedicated audio buffer, and diagnostic logging.

## [2.2.0] - 2026-06-13
- Direction-B visual redesign across the app, pill, icon and website; AEO artifacts for the
  site; Windows build refreshed. (Baseline for this changelog — earlier history in git.)
