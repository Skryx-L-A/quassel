# Quassel roadmap

Living plan for Quassel (local, private voice typing). Issue numbers refer to
GitHub `Skryx-L-A/quassel`. Effort: **S** = hours, **M** = a day, **L** = multi-day.
Status as of the `next` branch (2026-10-09): "released" means shipped in v2.7.0 or
earlier; "unreleased" means finished on `next` and part of the next version, but
not yet released or tried on a real device by the maintainer.

## Open

### Dictation and recognition
- #10 True streaming typing — word-by-word typing of confirmed words is released and
  optional (hands-free mode only); what is still open is incremental streaming that
  does not slide a 15-second window (LocalAgreement) — **L**
- #33 Wake word — still **beta**. Unreleased: an optional keyword detector in front of
  the speech engine and an insertion gate. It has only been tested with stand-ins, not
  with a real keyword model, and no model ships with Quassel — **M**
- #32 Voice command "press enter" — works as a whole utterance. "Enter at the end of a
  dictation" ("…, press enter") is open — **S**
- Other speech engines behind the `SpeechBackend` interface. `transcribe.cpp` runs in the
  benchmark: Cohere Transcribe and Parakeet v3 beat the current model on mixed German and
  English, but Cohere invents sentences on silence and needs a voice-activity gate first
  (`docs/measurements/2026-10-09-transcribe-cpp-vier-modelle/`). Open: wire it into
  dictation behind that gate and measure latency with the model kept loaded. A model only
  replaces the current one if German, mixed language and names are at least as good, or
  the speed gain carries it — **L**
- Latency: keep trimming the fixed waits per platform after each measurement
  (see `docs/next/PLAN.md`, phase 3) — **M**
- Personal dictionary: smarter selection of words when the list outgrows the prompt
  budget (by how often you use them) — **M**

### Interface
- #27 First-run onboarding — only a welcome screen exists. A step-by-step wizard
  (permissions, microphone test, model download, "try it now") is open — **M**
- #28 Settings redesign round 2: per-setting hints and live previews — **M**
- A control center page for dictation clean-up, per-app profiles, auto-stop, pre-roll and
  the keyword detector (today these live in `config.ini` and `profiles.ini`,
  see [docs/CONFIG.md](docs/CONFIG.md)) — **M**
- Model management in the control center: sizes, speed hints, a recommendation, delete — **M**

### Trust and distribution
- macOS notarization — needs an Apple Developer account ($99/yr). Without it the DMG
  costs every user one trip through System Settings → "Open Anyway"; the Homebrew cask
  and the Terminal installer clear the quarantine flag themselves, which is why they
  are the recommended paths — **S once the account exists**
- Windows code signing — removes SmartScreen "unknown publisher" — **M**
- Refresh the Windows installer. `Quassel-Setup.exe` has been carried over unchanged
  since 2.4.0 and needs a Windows machine to rebuild — **M**
- Packaging: an AUR draft exists but has not been tested on Arch; COPR and Flathub (#9) — **S/M/L**
- Distro testing: Ubuntu/Debian/Mint (#13), Arch (#14), openSUSE (#15) — help wanted

### Someday / maybe
- macOS: measure the Bluetooth microphone path (24 kHz) and word-error rate on a real
  voice — both were left unmeasured because acoustic tests need explicit consent — **S**
- Measure the shorter audio window on Windows. Linux is done (2026-08-04): `base-q5_1`
  and `small` hold the twelve-second threshold with more room than the large model, and
  a control run reproduced the Mac numbers exactly on different hardware. Windows runs
  the same shared code and the same model sizes, so this is a confirmation, not an open
  risk — **S**

## Unreleased (finished on `next`)
- #18 Per-app profiles — language, programmer mode, AI mode and an Enter block per
  application, with ready-made profiles for terminals, IDEs and mail. Opt-in through
  `profiles.ini`. Active app known on macOS, Windows, X11, Hyprland and Sway.
- #19 Voice activity detection — optional Silero model for the wake word and for
  hands-free auto-stop (`[vad] autostop`, off by default); the loudness threshold stays
  the fallback.
- Dictation clean-up (opt-in): conservative filler, repeat and number handling, with
  an optional local model step whose answer is checked (entities, numbers, negations,
  questions, language, markup) before anything is inserted. Best measured model so far:
  Qwen3-4B-Instruct at the strong level.
- Command-line client and local MCP server, SRT/WebVTT output for file transcription.
- Pre-roll buffer on Linux, macOS and Windows (`preroll_ms`, off by default).
- OpenAI-compatible servers (llama-server, LM Studio, vLLM) for clean-up and AI modes;
  "Raw text back" in the menus and on the command line; "Listening…" after the wake
  phrase; "Save as" for transcribed files; German "ß" restored for fine-tuned models.
- A microphone that drops out mid-dictation is reported instead of "too short"; a
  damaged settings file is kept as a copy instead of being overwritten; the history
  cannot lose an entry while old ones are removed.
- The command-line client ships with the Linux installers and the AUR package.
- "Copy diagnostics" in the control center (About page) and `quassel-type diagnostics`:
  version, settings with secrets removed and the recent log, never history, dictionary
  or dictated text.
- `QUASSEL_PROFILE` to run a second copy next to the installed one.
- Windows runs the same dictation flow as Linux and macOS (rescue of failed recordings,
  separate wake-word recording file, the stop-race fix).
- Privacy and robustness: history expires after 7 days by default and is readable only
  by you, last dictation and recordings are deleted after use, clipboard handling keeps
  dictated text out of clipboard history, the speech server uses a random URL prefix
  where the platform generates one, wake-word text is never logged, downloads are
  checked against checksums.
- Linux quick installer repaired and whisper.cpp pinned to a fixed version.
- Reference pages: [CONFIG](docs/CONFIG.md), [PRIVACY](docs/PRIVACY.md),
  [ARCHITECTURE](docs/ARCHITECTURE.md), [TROUBLESHOOTING](docs/TROUBLESHOOTING.md).

When the next version is released, issues #16, #17, #18, #19, #20, #21, #22, #25,
#26, #29, #30 and #31 can be closed: the features exist (see Done).

## Done (released)
- **v2.7.0 — macOS tones.** The start, stop and cancel tones follow the current
  output device; plug in headphones mid-session and the next tone goes there.
- **v2.6.0 — decoding measured instead of assumed.** Greedy search on every platform
  (beam search was never more accurate over 36 files and 938 reference words, and cost
  6 % more time), and a smaller audio window for dictations under twelve seconds (38–44 %
  less time in the speech server at an unchanged word error rate). Data and scripts:
  `docs/measurements/2026-08-03-audio-ctx-und-beam-size/`.
- **v2.5.0 — macOS port (Apple Silicon).** Menu-bar app, pill, `Ctrl+Cmd` dictation,
  Metal whisper.cpp, recording via sounddevice/CoreAudio. Distributed as a Homebrew cask
  (tap `Skryx-L-A/homebrew-quassel`), a DMG and a one-line installer.
- **v2.4.0 — "Hands-free & polish" and the "AI tier"** (both shipped together):
  - #16 Local AI post-processing (filler removal, grammar, formatting) via Ollama, opt-in
  - #17 Custom AI commands / prompt modes, built-in and custom, with voice triggers
  - #30 Smart formatting modes (email, bullets, paragraphs, formal, concise)
  - #20 Text replacement / snippets
  - #21 Audio-file transcription
  - #22 Local usage statistics
  - #25 Programmer dictation mode
  - #26 Pause media while dictating (Linux MPRIS, Windows SMTC; on macOS Music and
    Spotify only)
  - #29 Auto-add words to the personal dictionary
  - #31 Reset to defaults
  - In-app update check (opt-in)
  - Word-by-word streaming typing (optional), better mixed-language dictation
    (`language = mixed`; see the code-switching measurement in `docs/measurements/`)
  - Wake word (beta), first-run welcome screen, CHANGELOG and release-notes discipline

## Dropped
- #24 Privacy "0 bytes sent" indicator — dropped.
