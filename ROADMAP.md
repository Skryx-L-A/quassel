# Quassel roadmap

Living plan for Quassel (local, private voice typing). Issue numbers refer to
GitHub `Skryx-L-A/quassel`. Effort: **S** = hours, **M** = a day, **L** = multi-day.

## In progress — v2.3 "Hands-free & polish"
- #27 First-run onboarding wizard ("nothing to configure, just close it") — **M**
- #26 Pause media while dictating — Linux MPRIS + Windows SMTC — **S/M**
- #31 Reset app to defaults — **S**
- #10 True streaming typing — word-by-word emission (not multi-word chunks) — **L**
- Wake-word VAD — opt-in, default off, "Hey Quassel", stop on silence — **M/L**
- In-app update check (latest GitHub release vs installed version) — **M**
- #20 Text replacement / snippet expansion — **M**
- #29 Auto-add words to the personal dictionary — **M**
- #25 Programmer dictation mode (camelCase / snake_case / symbols by voice) — **L**
- #32 Voice command: "press enter" — **S**
- #21 Audio-file transcription (drop a file, get text) — **M**
- #22 Local usage statistics (words dictated, time saved) — **S**
- #23 Better mixed-language dictation (German + English in one sentence) — **L**
- CHANGELOG + release-notes discipline — **S**

## In progress — v2.4 "AI tier" (opt-in, still 100% local, via Ollama)
- #16 Local AI post-processing (filler removal, grammar, formatting) — **done (branch worktree-voxtype-ai-tier)**
- #30 Smart formatting (lists, paragraphs, emails) — **done** (email/bullets/paragraphs/formal/concise modes)
- #17 Custom AI commands / prompt modes ("turn this into an email") — **done** (built-in + custom + voice triggers)

## Next — v2.5
- #18 Per-app profiles (tone / mode / language per application) — **M**
  (now unblocked by the AI modes/languages above)

## Trust & distribution (parallel track)
- macOS notarization — needs an Apple Developer account ($99/yr). Without it the DMG
  costs every user one trip through System Settings → "Open Anyway"; the Homebrew cask
  and the Terminal installer clear the quarantine flag themselves, which is why they
  are the recommended paths — **S once the account exists**
- Windows code signing — removes SmartScreen "unknown publisher" — **M**
- "Copy diagnostics" button (bundles crash.log / debug.log / config) — **S**
- Settings redesign round 2: per-setting hints + live previews (#28) — **M**
- Packaging: AUR (#14), COPR, Flathub (#9) — **S/M/L**
- Distro testing: Ubuntu/Debian/Mint (#13), Arch (#14), openSUSE (#15) — help wanted

## Dropped
- #24 Privacy "0 bytes sent" indicator — dropped.

## Done
- Decoding measured instead of assumed — shipped in v2.6.0: greedy search on every
  platform (beam search was never more accurate over 36 files and 938 reference words,
  and cost 6 % more time), and a smaller audio window for dictations under twelve
  seconds (38–44 % less time in the speech server at an unchanged word error rate).
  Data and scripts: `docs/measurements/2026-08-03-audio-ctx-und-beam-size/`.
- macOS port (Apple Silicon) — shipped in v2.5.0: menu-bar app, pill, `Ctrl+Cmd`
  dictation, Metal whisper.cpp, recording via sounddevice/CoreAudio. Distributed as a
  Homebrew cask (tap `Skryx-L-A/homebrew-quassel`), a DMG and a one-line installer.

## Someday / maybe
- macOS: measure the Bluetooth microphone path (24 kHz) and word-error rate on a real
  voice — both were left unmeasured because acoustic tests need explicit consent — **S**
- Measure the shorter audio window on Windows. Linux is done (2026-08-04): `base-q5_1`
  and `small` hold the twelve-second threshold with more room than the large model, and
  a control run reproduced the Mac numbers exactly on different hardware. Windows runs
  the same shared code and the same model sizes, so this is a confirmation, not an open
  risk — **S**
- Refresh the Windows installer. `Quassel-Setup.exe` has been carried over unchanged
  since 2.4.0 and needs a Windows machine to rebuild — **M**
