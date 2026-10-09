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

### Fixed
- AI modes ("as an email …") now also work with OpenAI-compatible servers such
  as llama-server or LM Studio; before, they silently inserted the raw text there.
- Linux dictation now inserts text when the clipboard was empty. After a
  browser text copy that cannot be fully preserved, it leaves the dictated
  text available to paste and warns that the copied formatting was lost.
- macOS dictation returns to normal paste after an unreadable clipboard item
  has been handled by direct typing.
- When a dictation cannot be inserted, Quassel only says "Text is on the
  clipboard" if the clipboard really holds it. Otherwise (for example when
  Linux keeps a copied image untouched) it says the dictation could not be
  inserted and saves the recording so nothing is lost.
- Terminal protection now checks the app in front right before the text is
  inserted. Switching from a mail window to a terminal while Quassel is still
  transcribing no longer sends a line break or a spoken Enter there.
- In apps whose profile forbids Enter (terminals by default), line breaks
  inside a dictation are now inserted as spaces, so a spoken "new line" can no
  longer run the first half as a command.
- If something unexpected fails after a dictation was transcribed (for
  example the clipboard or a full disk), Quassel now saves the recording, shows
  an error and returns to idle instead of hanging on "transcribing".
- With pre-roll switched on, starting or emergency-stopping a dictation no
  longer waits while the idle microphone stream is being released.
- The emergency stop signal (`SIGUSR2`, the "Stop dictation now" menu item on
  macOS) can no longer freeze Quassel when it arrives while a recording is
  starting or ending.
- After saying only the wake phrase, the pill now shows the level of the
  wake recording and no longer warns about a silent microphone while you speak.
- Starting a dictation with the hotkey while the wake word is listening for
  your follow-up no longer stops that dictation's level display, plays the stop
  tone in the middle of it or shows the pill as idle.
- A damaged settings file is no longer replaced by the part that could still be
  read: Quassel keeps a copy next to it (`config.ini.damaged-…`) before saving.
- The dictation history could lose a new entry while expired entries were
  being removed; it no longer does.
- With the history switched off, MCP clients can no longer read old entries
  that are still on disk.
- The command-line launcher is now included in Linux installations and
  packages, so `quassel-type-cli` is available after setup.
- Saved transcripts and subtitles now use private file permissions.
- Profile settings on macOS and Windows no longer change the installed app's
  login startup setting; profile startup is unavailable on those platforms.
- Changing a model in a Linux profile now restarts that profile's speech
  server, so the new model takes effect without interrupting the installed app.
- Linux profile commands now start, stop and signal only the selected profile;
  stopping a profile leaves the shared keyboard helper running.
- Windows: when the audio driver stops responding while a recording ends,
  Quassel now says to restart it instead of claiming the microphone is busy.
- Model downloads from Hugging Face show real progress instead of jumping to
  100 % at once, and cancelling a download waits for it to stop before the
  partial file is removed.
- Windows recording now writes audio outside the capture callback and returns
  from a stalled device stop within a deadline.
- A slowly loading Windows GPU speech engine is no longer replaced with the
  CPU engine; a CPU emergency fallback now retries the preferred engine on
  the next start.
- The Linux pill opens Control Center from its own installation even when
  service startup omits Quassel from `PATH`.
- Linux now finds an existing Wayland socket instead of assuming `wayland-0`
  when the session does not provide a display name.
- Linux: switching Quassel on from the pill or with `quassel-ctl start` now
  hands the session's display to the dictation service first, so it types into
  the right window on Sway, Hyprland and other desktops that do not do this
  themselves.
- On Windows, unavailable media controls no longer toggle muted sound or start
  paused playback when dictation begins.
- Wake word (macOS and the portable Linux pill): "Listening…" after the
  phrase now shows above the pill even when the live preview is turned off,
  so you can see that Quassel is listening. Dictated preview text stays
  hidden as before.
- Linux and macOS: if the microphone stops delivering sound during a
  recording (unplugged, Bluetooth disconnected, system power event), Quassel
  now notices it and closes the dead input instead of keeping it open for the
  next dictation.
- Control center: a Whisper model you set up yourself now shows its file name
  instead of "small", and choosing it again switches back to it instead of
  loading a different model.
- Wake word: phrases and dictations with accented or non-German letters
  ("naïve", "Łódź", "é") no longer lose a word or cut the dictation in the
  wrong place after the phrase.
- Dictated questions and instruction-override examples now stay exactly as
  spoken when text cleanup or number formatting would change them.

### Added
- German "ß" is restored where spelling needs it when a German fine-tuned
  speech model is in use (it writes "grosses" instead of "großes"); "dass",
  "Masse" and English words stay as they are. Setting `[speech] eszett`
  (`auto`, `on`, `off`); keep it `off` for Swiss German.
- Linux: a second Quassel next to the installed one (`QUASSEL_PROFILE`) can
  run its own dictation service from the new template
  `systemd/quasseld@.service`. Its control center, pill and
  `quassel-type-cli raw-last` then check, switch and ask only that service,
  and turning it off no longer stops the installed app's shared helper.
- Wake word: after the phrase alone, the pill shows "Listening…" while it
  records your dictation; a tone plays only when signal tones are on.
- Transcribe file: "Save as…" writes the result as a text, SRT or WebVTT file.
  The suggested name is the source file's name with the matching extension.
- "Raw text back" without a new global shortcut: the macOS menu bar menu and the
  Windows tray menu have it as an entry, and on Linux `quassel-type-cli raw-last` asks the
  running dictation service to put the unedited text of the last dictation on
  the clipboard. The command never reads the text itself; it stays only in the
  service's memory and is never written to disk.
- AI page: "Refresh" and "Test" also work with OpenAI-compatible servers such
  as llama-server, LM Studio or vLLM. Quassel asks Ollama for its models first
  and otherwise reads the server's model list (/v1/models); an address ending
  in /v1 is treated as OpenAI-compatible straight away. "Test" then checks the
  same cleanup call that dictation uses. Local addresses never go through a
  system proxy.
- Windows now supports the optional pre-roll setting: it keeps up to two seconds of recent microphone audio for the next dictation and closes the input after the short warm period.
- Per-app profiles: the "While dictating" page has a button that creates the
  profiles file with the default profiles (terminal, mail, editor and so on) if
  it does not exist yet and opens it. An existing file is never overwritten.
  Long entries no longer make a settings page wider than the window.
- Beta features page: the pre-roll (0 to 2000 ms of audio kept from before
  the hotkey, Linux and macOS) can be set in the Control Center instead of only
  through an environment variable. The page says that the microphone stays
  open for three seconds after each dictation and, on macOS, that the
  microphone indicator stays visible during that time.
- Beta features page, wake word: you can pick a keyword model (openWakeWord
  format, trained yourself) that listens for the phrase before speech
  recognition runs, set its trigger threshold, and choose whether the phrase is
  confirmed by speech recognition before a dictation starts.
- Beta features page: auto-stop for hands-free dictation can now be switched
  on in the Control Center, with the pause length before it stops, the way
  pauses are detected (automatic with an optional Silero model, or loudness
  only) and a file picker for the Silero model.
- Control Center, AI page: a "Clean up dictation" section. It is off by
  default; the levels are Off, Light (fillers and repeats, no AI), Medium and
  Strong (with the local AI). It explains that Quassel inserts your dictation as
  spoken whenever the cleanup fails or would change numbers, names, links or
  the meaning. "Raw text back" puts the unedited text of your last dictation on
  the clipboard; Quassel keeps that text only in memory, never on disk, and
  never types it into a window on its own.
- The first-start window now walks you through setup in five short steps you
  can skip at any time: the hotkey, the permissions Quassel needs (on macOS
  with what is still missing and a button to the right System Settings page),
  a microphone test that shows how loud you were without playing anything
  back, whether the speech model is ready, and a field to try dictating.
- The optional text-cleanup benchmark can save case IDs and safety outcomes
  without saving dictated or generated text, and now reports how often the
  cleanup model was called.
- A command-line client can show Quassel's local status, manage the dictionary
  and installed speech-model selection, transcribe an existing file, and safely
  request the Linux service toggle or emergency stop. It never starts a
  microphone recording or downloads a model unless `--download` is explicit.
- Quassel can now be used as a local stdio MCP server for file transcription,
  dictionary management, and status. Dictation history is not exposed unless
  it is explicitly enabled in the MCP configuration.
- File transcription now returns source-relative timestamped segments when the
  local speech server provides them. The command line can write SRT or WebVTT,
  including for long files split into processing chunks, and file-processing
  callers can show progress or cancel safely.
- Optional pre-roll on Linux and macOS: set `QUASSEL_PREROLL_MS=300` (up to
  2000 ms) to include a short audio buffer before the next dictation starts.
  The input stays open for three seconds after a dictation. The default is off.
  On macOS, the microphone indicator remains visible while the input is open.
- macOS: when Secure Input is on (a password field, or Secure Keyboard Entry
  in Terminal), the system hides every key from Quassel and the hotkey does
  nothing. The pill now says so for a few seconds when it starts, and the menu
  bar status line shows "Secure input is on, hotkey paused" for as long as it
  lasts.
- Prepared an optional, default-off dictation clean-up path with conservative
  German and English filler removal, number formatting, and a protected local
  AI step. When a clean-up result fails validation, the original dictation is
  kept. The settings and insertion flow still need to be connected before this
  option appears in the app.
- Per-app profiles: Quassel can use different settings depending on the app you
  dictate into. A profile can set the language, turn the AI clean-up on or off or
  pick an AI mode, switch programmer dictation and spoken punctuation, and forbid
  Enter. Where a profile turns programmer dictation on, you don't need to start
  with "Code:". With Enter forbidden (meant for terminals), "press enter" only shows
  "Enter is blocked in this app", a line break at the end of the text is dropped, and
  live typing writes line breaks as spaces, so a dictated command is never run by
  accident. Profiles live in `profiles.ini` next to `config.ini`; without
  that file nothing changes. Ready-made profiles cover terminals, IDEs and mail
  programs. On Wayland, the active app is only known under Hyprland and Sway;
  GNOME and KDE keep the normal settings. Window titles are never read.
  kept. The path now runs before a single final insertion when explicitly
  enabled in the configuration file; control center settings are still pending.
- Optional neural voice detection (Silero VAD) for the wake word and hands-free auto-stop.
  Quassel does not ship the model and never downloads it: put `silero_vad.onnx` into the
  models folder (or set `silero_model` under `[vad]` in `config.ini`) and install
  `onnxruntime` yourself. Without either, the built-in loudness threshold keeps working
  as before. `backend = rms` under `[vad]` switches the neural detector off.
- Optional hands-free auto-stop: with `autostop = true` under `[vad]` in `config.ini`,
  a double-tap dictation ends by itself after a pause (`autostop_silence`, default 1.5
  seconds), but only after you have actually spoken, so an accidental start never ends
  an empty recording. Off by default. Works the same on Linux, macOS and Windows.
- Optional wake-word detector in front of the speech engine. Instead of transcribing
  every sentence said in the room, Quassel can listen with a small keyword model
  (openWakeWord format) and only start the speech engine once it hears the wake word.
  Quassel does not ship such a model: train or download one, then set `spotter_model`
  (and optionally `spotter_threshold`, `verify`) under `[wakeword]` in `config.ini`;
  `onnxruntime` must be installed. Without a model, the wake word works as before.
- For measurements only (the app still uses its whisper server): the benchmark
  script can run other speech models through transcribe.cpp, such as Cohere
  Transcribe, Qwen3-ASR, Canary and Parakeet, and its report names the model
  file, model family and quantisation. A build script and the pinned model
  downloads with checksums are in `scripts/tcpp/`.

### Changed
- Clean up dictation: the Medium and Strong levels now ask the local AI for
  different work. Medium removes only fillers, repeats and false starts and fixes
  punctuation; Strong also smooths sentence structure and grammar. Both keep
  facts, names, numbers, links and the meaning.
- Transcribe file: you can choose plain text or subtitles (SRT or WebVTT),
  follow the progress and cancel a long file. The result is no longer copied to
  the clipboard automatically, which used to replace whatever you had copied;
  press Copy when you want it.
- The Control Center has shorter pages, each about one thing: General (status
  and hotkey), Pill, While dictating (sound, media and live typing), Speech,
  Microphone, and About on its own. Unfinished features such as the wake word
  are collected on a "Beta features" page instead of sitting at the bottom of
  the speech settings.
- Fewer system notifications. Pressing the hotkey together with another key
  (for example to switch desktops) now ends quietly instead of announcing
  "Canceled (other key pressed)". A single tap and a recording that is too
  short are shown in the pill for a moment instead of as a notification; only
  with the pill switched off do they still come as a notification.
- Windows: the pill now shows the real microphone level too, flags a silent
  microphone with a flat amber line and a hint, gives each state its own shape,
  respects "Show animations" being off, and tells screen readers its state. Its
  text bubble uses the pill's colors and stays readable when the pill is set
  almost transparent.
- AI-enhanced dictation now finishes processing before inserting text once.
  It no longer replaces an earlier insertion with backspaces after a model
  response, which could erase text in a different window if focus changed.
- Linux starts checking clipboard contents while recording and uses fewer
  clipboard commands when inserting a dictation.
- The pill now shows your real microphone level while you dictate instead of a
  fixed animation. If nothing arrives from the microphone for 1.5 seconds, the
  bars turn into a flat amber line and the pill says so, so a muted or wrong
  microphone, or missing microphone access, is visible right away instead of
  after a dictation that comes back empty.
- Short hold-to-talk dictations get their text sooner. The live preview used
  to send its first request after two seconds, and the speech engine works on
  one request at a time: if you let go while that preview was being worked on,
  the final transcription waited behind it, on a CPU for seconds. When you hold
  the hotkey, the preview now starts only after four seconds; hands-free
  dictation keeps it from the start.
- Programmer dictation no longer rewrites ordinary prose. With the mode on, "Punkt gleich
  wichtig" became ".= wichtig" and "at" became "@" in every sentence. Code is now only
  formatted when you start the dictation with "Code:". If you relied on the mode being
  always active, say "Code:" first.
- Your own AI modes are only triggered by their full name. A mode called "tweet" used to
  fire on "Tweeted yesterday …" and ate the first five letters.
- With the AI clean-up on, Ollama now keeps the model loaded for 30 minutes
  after each use instead of its default five. Anyone who dictates with pauses
  of more than five minutes used to pay the cold start again, which takes 20 to
  30 seconds with a larger model. The model holds its memory for that long; it
  is released afterwards, as before.

### Fixed
- Per-app profiles: the page shows where the profiles file lives before you
  press the button. On macOS the file opens in the default text editor (an .ini
  file often has no app assigned there), on Windows in Notepad if no app is set
  for it. If no editor opens, the page says so and names the path instead of
  claiming it was opened.
- macOS: when the text could not be typed and is left on the clipboard, the
  pill now says to press ⌘V instead of Ctrl+V.
- When a speech server responds but cannot transcribe, Quassel can restart its own profile server once and retry the recording. The macOS app accepts restart requests from its own dictation process.
- Windows now stops dictation on sleep and checks the speech server after wake. Linux clears held shortcut keys after sleep so the next shortcut works.
- Windows microphone choices now follow the selected WASAPI device by name when device indexes change.
- macOS permission guidance now follows the app language and names the selected dictation shortcut. The microphone permission description is readable in English and German.
- Typing time estimates use the optional `[stats] typing_wpm` setting (10–200 words per minute; default 40).
- Turning off dictation on macOS now unloads Quassel's own speech server;
  turning it back on starts the server before dictation resumes.
- Linux Bluetooth output gets a short silence lead-in before the recording
  start tone, so the headset has time to wake before the audible part.
- Linux microphone choices are listed consistently when the system language
  is not English.
- On GNOME Wayland without layer-shell, the pill uses XWayland when available
  and reports when it may be hidden behind other windows.
- macOS music ducking also checks Podcasts and VLC and resumes only players
  that were playing before dictation.
- Pasting on macOS follows the current keyboard layout, including layouts
  where the V key has a different physical position.
- Bluetooth microphones are recognized in the packaged macOS app even when
  framework discovery is unavailable.
- When a dictation fails, Quassel now says why instead of "Whisper server
  unreachable" for everything: the speech model is missing (with its name),
  the server is not answering (it may still be loading), it took too long, or
  it could not transcribe the recording. The rescued recording is still named.
  If the microphone cannot be opened, the message says whether macOS denied
  access, the recording program is missing, or the microphone is busy or
  unplugged, and the pill shows it too. Windows no longer shows raw error
  texts from the setup. A slow server is no longer asked a second time over
  curl, which made a timeout take twice as long.
- On Wayland systems with a recent wl-copy, Linux marks dictated clipboard
  text as sensitive so compatible clipboard managers can omit it from history.
- Linux leaves images, files and rich clipboard contents alone when inserting
  text and keeps anything you copy during the restore delay.
- macOS preserves every pasteboard item and format across dictation insertion.
  If an item cannot be read, Quassel types without changing the pasteboard.
- Windows keeps dictation text out of clipboard history and cloud sync. It
  preserves common image, file and rich text clipboard formats, and types
  directly when a clipboard format cannot be safely restored.
- Windows restores an earlier clipboard item only if it still holds Quassel's
  own dictation text, so a new copy made while dictating is kept.
- Windows now stops only a verified Quassel speech server process, so other
  speech server processes are left alone.
- macOS and Windows keep bounded speech server diagnostic logs in the profile
  data folder to make startup failures easier to diagnose.
- On macOS and Linux, a detected sleep event now cancels an active dictation;
  waking checks the microphone selection and local speech server again.
- macOS now shows a notification and a pill error if its dictation service
  crashes unexpectedly, with an option to turn dictation back on.
- A failed transcription now checks whether the local speech server can
  process a short silence clip. If Quassel owns a failed server child, it
  restarts it once and retries the dictation once.
- macOS: selecting the system default microphone now follows changes made
  between dictations when the device is already in the audio device list.
- Linux recording now begins before the Bluetooth card check, avoiding a
  delay that could cut off the first word.
- The microphone test in the Control Center now says what went wrong instead
  of "Whisper server unreachable" for everything: missing microphone access on
  macOS (with the place to allow it), a microphone that is busy or unplugged, a
  missing speech model, a server that is still starting, or a recording the
  server could not transcribe.
- Linux: turning off "Click on the pill opens the control center" now also
  works for the standard pill. The settings page also explains that on Linux
  only the portable pill can be moved, and only under X11.
- The microphone list in the Control Center has a Refresh button, so a
  microphone you plug in shows up without reopening the window. A saved
  microphone that is unplugged at the moment stays selected instead of being
  silently replaced by the system default.
- The Control Center works better with screen readers and the keyboard: every
  field has a name, labels are linked to their fields (clicking a label moves
  the focus there), the keyboard focus is visible in the sidebar and on the
  main button, and the grey help texts and the amber beta warning now have
  enough contrast in light and dark mode. The beta warning used to reach only
  about 2:1 on a light background.
- Moving the pill size or transparency slider now saves once when you let go
  (or after a short pause when you use the arrow keys), and typing an AI model
  name saves after you stop typing. Each step used to rewrite the settings file
  and make the dictation service reload it, dozens of times per drag. A remote
  AI address you have confirmed is no longer asked about again on every change.
- The text bubble above the pill (live preview, results, hints) now has its
  dark background behind the text again and stays readable when the pill
  itself is set almost transparent. The background was drawn as a small piece
  in a corner, so the text stood on whatever was on your screen.
- The pill is easier to read without relying on color or motion: each state
  has its own shape (off, ready, transcribing, done, error), screen readers get
  its name and current state, and with "Reduce motion" turned on in the system
  settings the pill stops animating and shows the level as a calm bar.
- On macOS the dictation service log (`~/Library/Logs/Quassel/daemon.log`) no
  longer grows without limit. From about 1 MB on it moves to `daemon.log.1`,
  the one before to `daemon.log.2`; older logs are dropped, so it takes up
  about 3 MB at most.
- On macOS the hotkey is now named with Mac keys: "Control + Command (⌃⌘)",
  "Option + Command (⌥⌘)" and "Control + Option (⌃⌥)". The Control Center used
  to call it "Ctrl + Meta (Windows key)".
- The macOS warnings about a missing ffmpeg and about a dictation service that
  keeps restarting are now shown in English as well, and the restart warning
  points to the log file that is actually used.
- The macOS menu bar menu is now in English or German, following the app
  language, and switches right away when you change the language in the
  Control Center. It used to be German only ("Bereit", "Ausschalten",
  "Beenden"). The last item is now called "Quit Quassel".
- On macOS, Quassel now follows the language you picked in System Settings when
  the app language is set to "Automatic". Started from the Finder or at login,
  it used to fall back to English even on a German system, because only the
  terminal sets the usual language variable. Windows now asks the system for
  its display language as well.
- README, INSTALL and the website no longer point Linux and Windows users to offline
  packages "in the latest release": those exist only for v2.2.0. The pages now say so
  and link that release, and the Linux quick-installer text describes what it really
  does (sets up the speech engine, building it when no prebuilt one exists).
- The Linux quick installer works again. Releases after v2.2.0 never had the
  prebuilt speech engines it asked for, so the one-line install stopped with
  "Engine download failed". It now checks the release first and, when the engine
  package is not there, builds the speech engine itself (a few minutes) and says
  so. Downloaded engines are checked against a checksum.
- The Linux installer builds a fixed, known version of whisper.cpp (v1.9.5)
  instead of whatever was newest that day, and rebuilds it when that version
  changes. An installation made earlier is moved to the same version on the
  next run.
- `./install.sh --model small-q5_1` now also switches an existing installation
  to that model. Before, it only downloaded the file and left the old model
  active. Without `--model`, a model you already chose is kept.
- Windows now runs the same dictation flow as Linux and macOS instead of its own
  copy, which had drifted apart. Three Windows problems go away with it: a failed
  dictation (speech engine not reachable or transcription error) is saved as a
  WAV file and the message names it, as already promised for all platforms; the
  wake word no longer records into the same file as a keyboard dictation, so a
  dictation with the wake word switched on cannot come out mixed or empty; and a
  new dictation started right after releasing the hotkey no longer stops itself
  and transcribes the wrong audio. Windows also gets the start-tone trim of the
  other platforms (160 ms instead of 120 ms) and a tray entry "Stop dictation
  now" that ends a hanging dictation and drops its text.
- Windows: with fast user switching or Remote Desktop, a second user's Quassel
  no longer quits right after starting because it found the first user's
  instance. The single-instance channel is now per user and session, and only
  your own user account can use it to open the settings.
- Your last dictation no longer stays on disk. On macOS and Windows the raw
  recording, the WAV sent to the speech engine, the preview audio and the last
  dictated text sat in Application Support or LOCALAPPDATA until the next
  dictation, and with that in backups. The audio is now deleted as soon as the
  text is inserted (or the dictation is canceled or too short), the wake-word
  audio right after it is checked, and the text in the status file is blanked
  a few seconds after the pill has shown it. A recording that had to be rescued
  keeps only its rescue file; if even the rescue fails, the raw recording stays
  as the last copy.
- Linux: if your keyboard disappears while you hold the hotkey (a Bluetooth
  keyboard falls asleep, a hub disconnects), the dictation now ends as if you
  had let go, instead of recording forever. Keys that were held on the vanished
  keyboard no longer count as pressed, so the hotkey works again right away.
  Hold-to-talk recordings also stop after five minutes, like hands-free ones.
- Linux: a dictation that hangs (the clipboard owner does not answer, the speech
  engine stalls) no longer freezes Quassel for good. After a minute the log
  says so and records where it is stuck, and the emergency stop now exists on
  Linux too: `systemctl --user kill -s USR2 quasseld` ends the dictation, drops
  its text and makes the hotkey usable again.
- macOS can again copy history and file-transcription results without relying
  on the Linux-only `xclip` command.
- Known Whisper subtitle and “for watching” silence artifacts are discarded;
  ordinary “thank you” and “vielen Dank” dictations stay intact when speech is present.
- Automatic dictionary learning no longer picks up junk or deletes your own
  words. It used to learn things like `2nd`, `10pm`, `5EUR`, `100km`, `20er`,
  `3D` and `OK`; numbers with a unit or ordinal ending and a few everyday
  capitals are now skipped, while names and technical terms such as `K8s`,
  `GPT4` or `PyTorch` are still learned. And once the dictionary reached 500
  entries it cut off the oldest ones, which are the ones you typed yourself.
  Now the limit only stops new words from being added; nothing that is already
  in the dictionary is removed.
- Settings, dictionary, text replacements, AI modes, history and statistics are
  now saved atomically. Each of them used to be emptied first and refilled
  afterwards, so a crash or a power cut in that moment left an empty file, and
  everything in it was silently gone. Only the speech-engine settings file had
  this protection so far. Now you see either the old file or the complete new
  one, and a failed save leaves the old one in place.
- A hand-edited or unusual `config.ini` can no longer stop Quassel. A `%` in a
  setting (a wake phrase, the AI address) made saving the settings fail and
  reading them fail with an error, and a decimal comma such as `hold_min = 0,5`
  kept the dictation service from starting at all, so it restarted over and
  over. The comma is now read as a decimal point; a value that is not a number,
  is out of range, or is not a yes/no answer falls back to its default instead
  of raising an error. An unreadable settings file no longer blocks saving new
  settings either.
- A dictionary word starting with `@` or `<` (a handle, `<3`) no longer breaks
  every dictation. The first dictionary word is sent to the speech engine as
  part of the request, and `curl` read such a word as the name of a local file;
  the transcription then failed and only the "server not reachable" message
  showed. All text fields are now sent as plain text.
- Spoken punctuation no longer turns ordinary uses of "Punkt", "period", or
  decimal commas into punctuation. "Punkt" and "period" now mark a sentence
  only at the end of a dictation.
- Quassel no longer inserts spaces inside spoken domains, file names, or
  abbreviations such as `github.com`, `config.json`, and `e.g.`.
- Automatic sentence capitalization now also handles non-ASCII letters such as
  `é`, `ñ`, and `ø`.
- Text replacements now run once per dictation. An expansion can no longer
  accidentally activate a second replacement rule.
- Voice-triggered AI modes now require the explicit address "Quassel,". This
  keeps ordinary sentence openings such as "Formal gesehen …" intact.
- The one-word commands "Enter" and "Undo" are now ordinary dictation unless
  they are addressed as "Quassel, Enter" or "Quassel, Undo".
- When German or English is selected for speech, spoken punctuation now uses
  only that language's rules.
- The wake word's speech detection counts a connected stretch of speech instead of adding
  up every loud moment. Clicks and bumps spread over a few seconds used to add up to "the
  user is speaking"; now they do not, and a click during the pause after a sentence no
  longer keeps the recording open. The loudness threshold follows the room's background
  noise and works the same on Linux, macOS and Windows.
- The wake word is recognised more reliably when the speech engine writes the made-up word
  as it sounds. "Hey Castle", "He Quassel", "Heykassel" or "Ok, hey Quassel" now
  count, while similar-looking but different words ("Hey Russel") and very short
  phrases are held to a stricter match.
- A wake-word dictation is no longer typed into a window while you have started a keyboard
  dictation in the meantime. The check now happens right before typing, not only before
  listening, and the two ways of typing take turns.

### Security
- The dictation history file (`history.jsonl`) is now readable by you only.
  It used to be created with the system's default rights, which on most systems
  let every other account on the computer read your last dictations. An
  existing file is tightened the next time something is dictated. The history
  itself stays on by default and can be switched off as before.
- The AI clean-up no longer goes through the system proxy when Ollama runs on
  this computer. Behind a company or system proxy, your dictated text was sent
  to the proxy on its way to `127.0.0.1`, or the clean-up failed without a
  message. Calls to this computer now go straight there; an endpoint elsewhere
  on the network keeps using the proxy settings.
- With the wake word switched on, the log no longer contains what was heard.
  The listener transcribes everything said in the room, and every sentence used
  to be written to the log in plain text. It now records only how long the
  phrase was and whether it matched the wake word.

## [2.7.0] - 2026-08-04

> macOS only. Nothing in this release changes Linux or Windows; both keep playing
> the tones through their own system player, which has always followed the
> system output.

### Fixed
- macOS: the start, stop and cancel tones came out of the built-in speakers even
  when the headphones were the system output device. The tones now play on
  whatever output macOS is using at that moment, and they follow a change while
  the app is running — plug in headphones mid-session and the next tone is
  already there. The cause was the audio library the tones went through: it
  reads the default output device once, when the app starts, and never looks
  again. Connect a headset afterwards and it stayed invisible; it was not even in
  the device list. Measured on a MacBook Pro with a Bluetooth headset: the system
  said "MacBook Pro speakers", the library still answered with the headset it had
  seen at launch. The tones now go through the macOS output unit that tracks the
  default device by itself.

### Added
- macOS: a new setting **"Play tones on"** in the control center, under the tone
  switch. It stays on "System default output" unless you change it, which is what
  almost everyone wants. Pick a fixed device when you want the tones somewhere
  else than the rest of your sound — on a headset while music keeps playing over
  the speakers, for instance. A device that is currently disconnected stays
  selected instead of falling back silently.

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
