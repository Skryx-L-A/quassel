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

### Added
- Quantized Whisper models (q5) in the model picker (`base-q5_1`, `small-q5_1`, `medium-q5_0`,
  `large-v3-turbo-q5_0`) — mainly smaller downloads / less RAM (CPU speed gain is small).
- Voice Activity Detection (Silero VAD): skips silence and stops phantom text on silence
  (e.g. "Thank you" / "Untertitel der Amara.org-Community") — a reliability win, ~free.

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
