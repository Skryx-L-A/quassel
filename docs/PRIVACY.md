# Privacy: what is stored where, and what leaves the computer

Quassel recognises speech on your own computer. There is no account, no
telemetry and no cloud service. This page lists everything Quassel stores, how
long it stays, and every network connection the code can make. Folder names
are explained in [CONFIG.md](CONFIG.md#where-the-files-live); the code behind
each statement is named so that you can check it.

## What is stored

| What | Where | Content | How long |
|---|---|---|---|
| Recording while you speak | runtime folder: `rec.raw`, `rec-b.raw`, `partial.wav`, `rec.wav`, `wake.raw` | Raw audio of the dictation in progress | Deleted when the dictation has been transcribed (`dictation.discard`). A crash can leave one file behind; it is overwritten by the next recording. On Linux the runtime folder is in memory (`/run/user/<uid>`) and disappears at logout. |
| Rescued recording | runtime folder: `rescued-<timestamp>.wav` | Audio of a dictation whose transcription or insertion failed, so it is not lost | Only created on failure. The five newest are kept; older ones are removed when a new one is saved. Delete them by hand when you no longer need them. |
| Status for the pill | runtime folder: `state.json` | State, the level of the microphone, and for four seconds the last text the pill shows | The text is removed from the file after four seconds. |
| History | data folder: `history.jsonl`, mode 0600 | The text of your last dictations, newest 50 at most | Entries older than 7 days are deleted (setting `history_days`; `0` keeps them until you clear the history). Turn it off with `[history] enabled = false` or in the control center, or press "Clear history". |
| Statistics | data folder: `stats.json` | Counts only: words, characters, seconds spoken and saved, and a per-day tally. No text. | Until you reset it in the control center. Turn it off with `[behavior] stats_enabled = false`. |
| Personal dictionary | settings folder: `dictionary.txt` | The words you added, plus words learned from your dictations if auto-learn is on | Until you delete them. |
| Auto-learn counter | settings folder: `dictionary-counts.json` | Candidate words (names, jargon) seen once, and how often, capped at two | Until you delete the file. Only exists if auto-learn is on. |
| Replacements, correction list, AI modes | settings folder: `replacements.txt`, `cleanup-corrections.txt`, `ai_modes.txt` | What you wrote | Until you delete them. |
| Settings | settings folder: `config.ini`, `profiles.ini`, `server.env` | Settings; `server.env` also holds the path and the random URL prefix of the local speech server and is written with mode 0600 | Until you delete them. "Restore all settings" removes `config.ini` and keeps the rest. |
| Copy of a damaged settings file | settings folder: `config.ini.damaged-<timestamp>`, mode 0600 | Your settings as they were before Quassel saved over a file it could not read completely | Only created in that case. Delete it once you have copied back what you need. |
| Saved transcripts | where you choose ("Save as" on the file transcription page), mode 0600 | Text, SRT or WebVTT of a transcribed file | Until you delete them. |
| Speech models | data folder: `models/` | Downloaded Whisper models | Until you delete them; `uninstall --purge` removes them. |
| Server log | data folder: `whisper-server.log` (macOS, Windows) | Output of the speech server. The server is started so that it does not print transcripts. | Rotated by size. |
| Daemon log | systemd journal (Linux), `~/Library/Logs/Quassel/daemon.log` (macOS, three files of about 1 MB), `debug.log` and `crash.log` (Windows) | Timing and error messages. Quassel does not write dictated text, wake-word audio or the text of a key press to any log. The wake-word listener logs only lengths and whether the phrase matched. | The macOS and Windows logs rotate by size. |

Two things deserve a note:

- **The clipboard.** To insert text, Quassel puts it on the clipboard for a
  moment, sends a paste key, and then puts your previous clipboard back, but
  only if the clipboard still holds Quassel's own text. It marks the entry as
  confidential where the system supports that, so clipboard managers and
  cloud clipboards skip it: the "concealed" type on macOS, the exclusion flags
  for clipboard history and cloud clipboard on Windows, and `wl-copy
  --sensitive` on Wayland when your `wl-copy` is new enough. On X11 and on
  older `wl-copy` versions this marker does not exist, so a clipboard manager
  there may record the text.
- **Per-app profiles** need to know which application is in front. Quassel
  reads only the application's identifier (bundle id, process name or window
  class). It never reads, stores or logs window titles. Without `profiles.ini`
  the lookup is not done at all.

## The diagnostics report

"Copy diagnostics" on the About page of the control center, and
`quassel-type diagnostics`, build a plain-text report for bug reports
(`quassel/diagnostics.py`). It is put on the clipboard or, with "Save as
file…", written with mode 0600. Nothing is sent anywhere; you decide whether
to paste it into an issue.

It contains:

- the Quassel version, the platform and the Python version;
- the profile name and the port of the speech server;
- the file name of the speech model (not its folder);
- the settings in `config.ini` and `server.env`. Device names (microphone,
  output for the signal tones) and your wake phrase stay in, because many
  audio problems depend on them; a device name can contain a name you gave
  the device;
- whether the local speech server answers (a request to its start page, never
  to `/inference`);
- the names of your per-app profiles in `profiles.ini`, without their app
  patterns or settings;
- the last 200 lines of the daemon log (the macOS `daemon.log`, the Windows
  `debug.log`, or on Linux `journalctl --user -u quasseld.service`, stopped
  after five seconds).

It never contains your history, personal dictionary, replacements, correction
list, AI mode texts, the clipboard or dictated text. Those files are not even
opened. Values of keys whose name contains token, key, secret, password, auth
or `REQUEST_PATH`, user names and passwords in addresses, and `Bearer` tokens
are replaced by `<entfernt>`; your home folder is written as `~`, and on Linux
the computer's name in journal lines as `<host>`. The log never holds dictated
text by design. As a second safeguard, every log line that could still hold
some is replaced by a notice: lines with `text=`, `hyp=`, `transcript=`,
`raw=`, `prompt=` and similar keys, and lines with more than 20 characters in
quotation marks unless that is a file path. If one part of the report cannot be
collected, the report says why and contains the rest.

## What can use the network

Quassel's own code makes these connections and no others. Everything is
over HTTPS unless noted.

| When | To | What is sent | Can you turn it off? |
|---|---|---|---|
| Installing, or choosing a model in the control center, or `model use --download` | `huggingface.co` | A request for the model file. The download is checked against a SHA-256 checksum built into Quassel (`net.py`) and discarded if it does not match. | It only happens when you ask for a download. |
| Installing on Linux | the package servers of your distribution, `github.com` (clone of whisper.cpp at a fixed version, release assets) | Normal package manager and git requests | Install from the offline package of v2.2.0 instead. |
| Installing on Windows, or first launch | `github.com` (speech engine from the whisper.cpp releases), `huggingface.co` (model) | Download requests | The offline package needs no download. |
| Installing on macOS | `github.com` (installer, DMG and its checksum) | Download requests | Install from the DMG you already have. |
| Update check | `api.github.com` (`/repos/Skryx-L-A/quassel/releases/latest`) | An anonymous request; GitHub sees your IP address and a user agent, as with any web request | Off by default (`[system] update_check`). The control center button "Check now" does the same once, on request. |
| Optional AI step or clean-up | the address in `[ai] endpoint`, by default `http://127.0.0.1:11434` | The dictated text and your instruction | Off by default. With a loopback address the text stays on your computer and bypasses system proxy settings. If you enter any other address, the control center asks you to confirm, and warns again if it is not encrypted (`http://`). |

Everything else stays on the machine:

- The speech server listens only on `127.0.0.1`. Quassel talks to it without
  using a system proxy, so a corporate proxy never sees your audio.
- Transcribing a file with the command-line client or the MCP tool sends the audio
  to that local server only. Non-WAV files are converted by a local `ffmpeg`;
  the temporary WAV is removed afterwards.
- The MCP server speaks only over standard input and output with the program
  that started it. It has no recording tool. It reads only the file path the
  client names. Dictation history is not offered unless you set
  `[mcp] mcp_history = true` and the history itself is on; entries left on disk
  after you turned the history off are never shared.
- The pill tells screen readers about errors. The text of a finished dictation
  goes to the accessibility interface only when the pill preview is on.
- Keyboard events are read to detect the hotkey. Quassel does not store keys
  and does not log them.
- The wake word, if you turn it on, makes the microphone listen continuously.
  Audio is processed in memory and in a scratch file that is deleted after each
  utterance; with a keyword model, audio reaches the speech engine only after a
  hit. Nothing is sent anywhere.

## Checking it yourself

To see all outgoing connections of a running Quassel, use your system's tools
(for example `ss -tnp` on Linux or Little Snitch / `lsof -i` on macOS) while
dictating. With the update check and the AI step at their defaults you should
see only the local connection to the speech server.
