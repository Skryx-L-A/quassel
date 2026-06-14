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
- Windows code signing — removes SmartScreen "unknown publisher" — **M**
- "Copy diagnostics" button (bundles crash.log / debug.log / config) — **S**
- Settings redesign round 2: per-setting hints + live previews (#28) — **M**
- Packaging: AUR (#14), COPR, Flathub (#9) — **S/M/L**
- Distro testing: Ubuntu/Debian/Mint (#13), Arch (#14), openSUSE (#15) — help wanted

## Dropped
- #24 Privacy "0 bytes sent" indicator — dropped.

## Someday / maybe
- macOS port (whisper.cpp runs; paste via CGEvent) — **XL**
