# Windows build & verify — Quassel v2.3.0

This release was built and tested on **Linux** only. Everything Windows-specific
below was written by mirroring the Linux paths but could NOT be run here. Build
on the Windows box and verify the checklist before publishing.

Branch with all the work: `worktree-voxtype-ui-polish` (local; not pushed). Nothing
is published or merged — the maintainer verifies first.

## What changed since 2.2.0 (all shared unless noted)
- **Streaming now types word-by-word** (`streaming.py`, default mode `word`). Shared.
- **Wake word** (opt-in, off by default; phrase "Hey Quassel", editable). New:
  `wakeword.py` + `vad.py`. Wired into BOTH daemons (Linux `daemon.py`, Windows
  `win/app.py._sync_wakeword`). **Windows-specific code = untested.**
- **Press-Enter voice command** (#32): `textproc` action `enter`; Windows
  `win/paste.send_enter` (SendInput VK_RETURN). **Windows-specific.**
- **Media pause while dictating** (#26): already implemented both platforms
  (Linux MPRIS, Windows SMTC in `win/audioctl`); now exposed in settings.
- **Text replacements** (#20), **programmer mode** (#25), **auto-learn** (#29),
  **usage stats** (#22): applied in the finish pipeline of BOTH daemons
  (`_refine` + `_after_insert`). Windows copy mirrors Linux.
- **Mixed German+English** (#23): `whisperclient` shared — works on Windows.
- **Audio-file transcription** (#21): `transcribe_file.py` — needs **ffmpeg on PATH**.
- **Update check**, **reset to defaults** (#31), **first-run onboarding** (#27):
  all in the shared `center.py` — appear on Windows automatically.
- Version bumped to **2.3.0**.

## Build (same as windows/build.md)
1. On the Windows box, pull/copy this branch.
2. Python 3.13 (NOT 3.14). `pip install -r` the build deps + PySide6 + pyinstaller.
   For media-pause backends, ensure `pycaw` and `winrt` (or `winsdk`) are installed
   so SMTC pause works (else it falls back to the media-key tap).
3. Build the app with PyInstaller (`--clean`) and the installer with Inno Setup
   (`iscc`), per `windows/build.md`.
4. Quick backend probe before full testing: run `Quassel.exe --audiocheck` and read
   `%LOCALAPPDATA%\Quassel\audiocheck.json` — confirm `have_pycaw` / `have_smtc`.

## Verify checklist (hands-on)
Core (regression):
- [ ] Hold Ctrl+Meta, speak, release → text pastes at cursor.
- [ ] Double-tap → hands-free → press once → pastes.
- [ ] Tray icon + pill show the new waveform; taskbar icon correct.

New in 2.3:
- [ ] **Streaming word-by-word**: enable Live typing (Speech page), hands-free dictate
      a sentence → words appear ONE AT A TIME (not multi-word blocks); later words may
      get corrected. (Mode dropdown shows "Word by word".)
- [ ] **Press Enter**: dictate just "press enter" → an Enter is sent (and the live-typed
      "press enter" is removed if streaming). Also "drücke enter".
- [ ] **Scratch that** still deletes the last dictation ("lösch das").
- [ ] **Media pause**: General → While dictating → "Pause music / media"; play music,
      dictate → it pauses and resumes after. Then try "Mute all system sound".
- [ ] **Text replacements**: Replacements page, add `omw=on my way`, enable, dictate
      "omw" → expands.
- [ ] **Programmer mode**: Speech page toggle on; dictate "camel case user name end case"
      → `userName`; "foo dot bar open paren close paren" → `foo.bar()`.
- [ ] **Auto-learn**: Dictionary page toggle on; dictate a sentence containing e.g.
      "PyTorch" → it appears in the dictionary afterward (ordinary German nouns must NOT).
- [ ] **Mixed language**: Speech → language "Mixed (German + English)"; dictate a German
      sentence with English words → English words stay English (better than before).
- [ ] **Wake word** (the riskiest new Windows path): Speech → enable wake word, phrase
      "Hey Quassel". Say "Hey Quassel, schreib einen Test" → after a short silence it types
      "schreib einen Test". Say just "Hey Quassel", pause, then speak → it dictates the
      next utterance. Turn it OFF → listener stops (no mic indicator). Watch CPU.
- [ ] **Audio-file transcription**: Transcribe file page → pick an mp3/mp4 → text appears
      and is copied to clipboard. (Requires ffmpeg on PATH; without it the page says so.)
- [ ] **Usage stats**: System page shows words/sessions after a few dictations; reset works.
- [ ] **Update check**: System → "Check now" → reports up-to-date or newer; "Get it" opens
      the releases page.
- [ ] **Reset to defaults**: System → reset → settings revert; dictionary/history/model kept.
- [ ] **Onboarding**: delete `%APPDATA%\Quassel\config.ini`, start the app, open settings →
      welcome wizard appears once, mentions changing the wake phrase; after closing,
      `onboarded=true` and it does not reappear.

## If something is wrong
Windows-specific suspects (Linux-verified logic, Windows-unverified):
- `win/paste.send_enter` (SendInput VK_RETURN).
- `win/app.py` wake-word: `_wake_record_utterance` (uses `win.audio_win.Recorder`),
  `_wake_transcribe`, `_wake_insert`, `_sync_wakeword` (called from the 1s `reload_chord`).
- `_refine`/`_after_insert` in `win/app.py` (mirror of `daemon.py`).
Check `%LOCALAPPDATA%\Quassel\debug.log` and `crash.log`.

## Handback
Report pass/fail per box. If green: rebuild `Quassel-Setup.exe` + offline package as in
the 2.2.0 flow; the maintainer publishes (GitHub release + Hugging Face + Netlify site).
