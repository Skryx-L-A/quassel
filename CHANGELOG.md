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

### Changed
- Streaming typing now appears word by word as you speak, instead of in larger multi-word
  chunks — words may still be refined afterwards as the recognizer hears more context.
- Better handling of mixed-language dictation (e.g. German with English words in one sentence).

### Removed
- (none)

### Fixed
- (none)

## [2.2.0] - 2026-06-13
- Direction-B visual redesign across the app, pill, icon and website; AEO artifacts for the
  site; Windows build refreshed. (Baseline for this changelog — earlier history in git.)
