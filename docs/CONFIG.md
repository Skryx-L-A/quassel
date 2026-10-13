# Configuration reference

Quassel needs no configuration: every setting below has a default, and the
control center writes the common ones for you. This page lists every key that
the code reads, with its default, the accepted range and what it does. It was
checked against `quassel/config.py`, `quassel/vad.py`, `quassel/wakeword.py`,
`quassel/profiles.py` and `quassel/cleanup.py`; a test
(`tests/test_docs.py`) fails when a key in `config.py` is missing here.

## Where the files live

| | Linux | macOS | Windows |
|---|---|---|---|
| Settings folder (`config.ini`, `profiles.ini`, `server.env`, `dictionary.txt`, `replacements.txt`, `ai_modes.txt`, `cleanup-corrections.txt`, `dictionary-learned.json`) | `~/.config/quassel` | `~/Library/Application Support/Quassel` | `%APPDATA%\Quassel` |
| Data folder (`history.jsonl`, `stats.json`, `models/`) | `~/.local/share/quassel` | same folder as settings | `%LOCALAPPDATA%\Quassel` |
| Runtime folder (`state.json`, scratch audio, `rescued-*.wav`) | `$XDG_RUNTIME_DIR/quassel` | `~/Library/Application Support/Quassel/run` | `%LOCALAPPDATA%\Quassel\run` |

`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are honoured on Linux. The models folder
is shared by every profile (see `QUASSEL_PROFILE` below).

`config.ini` is plain INI text. The control center rewrites only the keys it
changes and the daemon reloads the file when it changes on disk, so a hand
edit takes effect without a restart. A value that cannot be read never stops
Quassel from starting; it silently falls back to the default. Numbers accept a
decimal comma (`0,5`). Booleans in `config.ini` accept `true/false`, `yes/no`,
`on/off` and `1/0`. Changes to `config.ini` made from the control center go
through an atomic write, so a crash cannot leave half a file.

Settings with a range behave in one of two ways, shown as "clamped" or
"falls back" in the tables: an out-of-range value is moved to the nearest
limit, or it is replaced by the default.

## `config.ini`

### `[meta]`

| Key | Default | Meaning |
|---|---|---|
| `version` | written by Quassel | Format version of the file (currently `1`). Quassel migrates older files once and never rewrites a file with a newer version number. Do not edit. |

### `[hotkey]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `chord` | `ctrl+meta` | `ctrl+meta`, `alt+meta`, `ctrl+alt` | The modifier combination that starts dictation. On macOS `meta` is Command and `alt` is Option. Unknown values fall back to `ctrl+meta`. |
| `hold_min` | `0.5` | 0 to 10 seconds, falls back | A hold shorter than this counts as a tap and is cancelled. |
| `double_window` | `0.45` | 0 to 10 seconds, falls back | Two taps within this time start hands-free dictation. |

### `[speech]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `language` | `auto` | `auto`, `mixed`, or a language code such as `de`, `en` | Spoken language. `auto` lets Whisper detect it; `mixed` detects it and adds a German/English primer for code-switching; a code pins the language. |
| `punctuation` | `true` | boolean | Turn spoken punctuation ("comma", "new line") into characters. |
| `commands` | `true` | boolean | Voice commands such as "scratch that" and "press enter". |
| `eszett` | `auto` | `auto`, `on`, `off` | Write "ß" where German spelling needs it ("grosses" becomes "großes"; "dass", "Masse" and English words stay). `auto` turns it on only with a German fine-tuned model, which never writes "ß". Leave it `off` for Swiss German, which uses "ss". |
| `mic` | `default` | device name | Microphone to record from. `default` follows the system setting. |
| `programmer_mode` | `false` | boolean | Programmer dictation: spoken symbols and identifiers become code text. Globally it applies when a dictation starts with "Code:"; a per-app profile can turn it on for every dictation. |
| `auto_learn` | `false` | boolean | Learn names and jargon from your dictations once they appear in two dictations at least ten minutes apart. Learned words are kept apart from `dictionary.txt`, lapse after 30 days unused and after 90 days at the latest, and can be kept or removed in the control center (a removed word is never learned again). Spellings that are triggers in `replacements.txt` or `cleanup-corrections.txt` are never learned. Never active together with `programmer_mode`. |
| `preroll_ms` | `0` | 0 to 2000 ms, falls back to 0 | Pre-roll buffer. Above 0, the microphone stays open for about three seconds after a dictation and the next one includes this much audio from before the hotkey press. Linux, macOS and Windows. On macOS and Windows the system microphone indicator stays visible while the input is open. |

### `[pill]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `enabled` | `true` | boolean | Show the pill overlay. |
| `scale` | `1.0` | 0.6 to 2.0, clamped | Pill size. |
| `opacity` | `0.85` | 0.15 to 1.0, clamped | Pill opacity. |
| `show_preview` | `true` | boolean | Show the live transcript in the pill while you speak. |
| `movable` | `false` | boolean | Allow dragging the pill. On Linux this works only for the portable Qt pill under X11. |
| `click_opens_center` | `true` | boolean | A left click on the pill opens the control center. |
| `pos_x`, `pos_y` | `-1` | integers | Saved position. `-1` means automatic (bottom centre). |

### `[history]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `enabled` | `true` | boolean | Keep the last dictations in `history.jsonl` (at most 50 entries). |
| `history_days` | `7` | 0 to 36500, falls back | Entries older than this many days are deleted. `0` keeps them until you clear the history. |

### `[ui]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `language` | `auto` | `auto`, `en`, `de` | Language of Quassel's own interface. `auto` follows the system. |

### `[streaming]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `enabled` | `false` | boolean | Type into the target window while you speak. Hands-free mode only. |
| `mode` | `word` | `word`, `stable`, `aggressive` | `word` types confirmed words one by one, `stable` only stable words without corrections, `aggressive` confirmed words with the shortest delay. Other values fall back to `word`. |

### `[behavior]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `mute_while_dictating` | `off` | `off`, `music`, `all` | `music` pauses playing media while you dictate and resumes it afterwards; `all` mutes the whole system sound and restores it. |
| `text_replace` | `true` | boolean | Apply the rules in `replacements.txt` (see below). |
| `stats_enabled` | `true` | boolean | Count dictated words and estimated time saved in `stats.json`. Local only. |
| `beep` | `true` | boolean | Short tones when recording starts and stops. |
| `beep_output` | `system` | `system` or a device name | Output device for the tones. Selectable on macOS only; `system` follows the system default. |

### `[wakeword]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `enabled` | `false` | boolean | Listen for a wake phrase and then dictate hands-free. Beta. |
| `phrase` | `Hey Quassel` | text | The wake phrase. An empty value restores the default. The phrase is matched by sound, so small mis-hearings ("Hey Kassel") still count. |
| `spotter_model` | empty | path to an openWakeWord keyword model (`.onnx`) | Optional keyword detector in front of Whisper. Empty means off. Needs `onnxruntime` and `numpy`; the two helper models `melspectrogram.onnx` and `embedding_model.onnx` must sit next to the keyword model or in `<models folder>/wakeword`. Quassel ships no model and downloads none. If anything is missing, the normal Whisper-based wake word keeps working. |
| `spotter_threshold` | `0.5` | 0.05 to 0.99, falls back to the default for unreadable values, otherwise clamped | Score above which the keyword detector fires. |
| `verify` | `true` | boolean (anything other than `1`, `true`, `yes`, `on` counts as false) | After the keyword detector fires, Whisper still has to hear the phrase. Set to `false` to trust the detector alone. |

### `[vad]`

Voice activity detection decides whether a sound is speech. It serves the wake
word and the hands-free auto-stop.

| Key | Default | Range | Meaning |
|---|---|---|---|
| `backend` | `auto` | `auto`, `rms` | `auto` uses the Silero model when `onnxruntime`, `numpy` and the model file are present, and the built-in loudness threshold otherwise. `rms` always uses the loudness threshold. |
| `silero_model` | empty | path | Path to `silero_vad.onnx` (version 5 or 6). Empty means `silero_vad.onnx` in the models folder. A wrong path is not searched around; Quassel falls back to the loudness threshold. Quassel never downloads this file. |
| `autostop` | `false` | boolean (see note below) | A hands-free dictation ends by itself after a pause, but only after you have spoken. |
| `autostop_silence` | `1.5` | 0.4 to 30 seconds, clamped | Length of the pause that ends the dictation. |

Booleans in `[vad]` and `[wakeword]` are read more strictly than elsewhere:
only `1`, `true`, `yes` and `on` mean true, everything else (including a typo)
means false.

### `[system]`

| Key | Default | Meaning |
|---|---|---|
| `update_check` | `false` | Ask GitHub once at start whether a newer release exists. Once Quassel is set up, this is the only request the running app makes by itself (see [PRIVACY.md](PRIVACY.md)). |
| `onboarded` | `false` | Set to `true` after the welcome screen was shown. |

### `[ai]` (local AI post-processing, opt-in)

| Key | Default | Range | Meaning |
|---|---|---|---|
| `enabled` | `false` | boolean | Send dictated text through a language model before inserting it. |
| `endpoint` | `http://127.0.0.1:11434` | URL | Address of the model server (Ollama protocol). A non-loopback address asks for confirmation in the control center, because dictated text would leave your computer. |
| `model` | empty | model name | Model to use, for example `llama3.2:3b`. |
| `post_process` | `false` | boolean | Apply `post_mode` to every dictation. |
| `post_mode` | `cleanup` | `cleanup`, `email`, `bullets`, `paragraphs`, `formal`, `concise`, or a custom mode name from `ai_modes.txt` | Mode used when `post_process` is on. |
| `voice_modes` | `true` | boolean | A dictation that starts with a phrase such as "as an email" is reshaped for that dictation only. |
| `timeout` | `60.0` | 1 to 3600 seconds, falls back | How long to wait for the model. Large models need 20 to 30 seconds on the first call. |

If the model is unreachable or answers with nothing, the unmodified text is
inserted. The AI step never drops a dictation.

### `[cleanup]` (dictation clean-up, opt-in)

A separate, conservative path that runs before the single final insertion. It
has no control center page yet; enable it in the file.

| Key | Default | Range | Meaning |
|---|---|---|---|
| `enabled` | `false` | boolean | Master switch. |
| `level` | `aus` | `aus`, `leicht`, `mittel`, `stark` (case does not matter) | `aus` does nothing. `leicht` removes clear filler words ("äh", "um"), immediate repeats and simple self-corrections, applies your correction list and normalises numbers, all locally and without a model. `mittel` and `stark` additionally ask the model from `[ai]` to polish the text (`stark` allows larger edits). Other values mean `aus`. |
| `decider_url` | empty (off) | an `http://` or `https://` address on this computer (`localhost`, `127.0.0.1`, `::1`) | Optional second check for `mittel` and `stark`: a local typed decision service that speaks the Jev format (`POST <url>/v1/systemone`). After the checks below accept an edit, Quassel asks it whether the cleaned text says the same as the dictation and keeps the edit only on a confident yes. Any other answer, or no answer, keeps the text as transcribed. An address on another computer is ignored. Example: `http://127.0.0.1:8095` (the workbench decision service with Decider-4B); the full `…/v1/systemone` address works too. A yes counts only at a probability of 0.85 or more, and the service is asked only when the clean-up changed words. |
| `decider_timeout` | `2.0` | 0.1 to 30 seconds | How long to wait for the decision service. |

The model's result is checked before it is used. When it is empty, too long or
too short, starts like an answer ("Sure, here is"), changes a number, URL,
e-mail address, code fragment or dictionary term, drops a negation or a date
word, turns a question into a statement, or adds sentences, the whole clean-up
is dropped and the text as transcribed (with spoken punctuation applied,
before any clean-up) is inserted instead. The same happens when the model is
unreachable, times out or answers in the wrong format. For `mittel` and
`stark` the `[ai]` keys `endpoint`, `model` and `timeout` choose the model;
the request goes to `<endpoint>/v1/chat/completions` (any OpenAI-compatible
local server, such as Ollama, llama-server or LM Studio) and `[ai] enabled` is
not required. With no model set, `mittel` and `stark` fall back to the text as
transcribed.

These checks compare words, numbers and entities. They do not see a swapped
role or an opposite word ("rejected" turned into "accepted"); in a measurement
on 40 synthetic pairs the `stark` level let 11 of 20 such changes through. The
optional `decider_url` closes that gap; a reference decision model judged all
40 pairs correctly.

### `[stats]`

| Key | Default | Range | Meaning |
|---|---|---|---|
| `typing_wpm` | `40` | 10 to 200 | Your typing speed in words per minute. The statistics page uses it to estimate the time dictation saved. |

### `[mcp]`

| Key | Default | Meaning |
|---|---|---|
| `mcp_history` | `false` | Expose a `history_list` tool to the local MCP client. Works only while `[history] enabled` is `true`; with the history off, entries left on disk are never shared. See [cli.md](cli.md). |
| `mcp_ask` | `false` | Expose the `ask_user` tool: an MCP client may wait for your next dictation, which you start yourself with the hotkey, and receive it as text instead of having it inserted. The microphone never starts on its own. See [cli.md](cli.md). |

## `profiles.ini` (per-app profiles, opt-in)

Without this file nothing changes, and Quassel does not even look up which
application is in front. Create it with the shipped examples using
`profiles init` of the command-line client (see [cli.md](cli.md)); `profiles list`
shows what was parsed and `profiles path` prints the location.

Each profile is a section named `[profile:<name>]`. The first profile whose
`match` fits the active application wins. A profile only changes what it names
and only for the dictation that goes into that application; `config.ini` is
never touched.

| Key | Values | Meaning |
|---|---|---|
| `match` | comma-separated patterns | macOS bundle id, process name or window class, case-insensitive, with `*` and `?`. One identifier matching one pattern is enough. Required; a profile without patterns is ignored. |
| `language` | `auto` or a 2 to 3 letter code | Recognition language and punctuation language. |
| `ai` | `on`, `off` | Turn the AI step on or off for this app. |
| `ai_mode` | a mode name, or `off` | Apply this mode to every dictation. `off` means no automatic mode; spoken voice modes still work. |
| `programmer_mode` | `on`, `off` | `on` works here without the "Code:" address that the global switch requires. |
| `punctuation` | `on`, `off` | Spoken punctuation. |
| `allow_enter` | `on`, `off` | `off` is the terminal guard: "press enter" is refused (the pill says "Enter is blocked in this app"), a line break at the end of the text is removed, and live typing writes line breaks as spaces. |
| `paste_delay_ms` | 0 to 2000 | Extra pause before the paste keystroke, for an app that needs it. Without it Quassel waits only until the clipboard holds the text (at most 250 ms on macOS and Linux, 150 ms on Windows). |
| `enabled` | `on`, `off` | `off` disables a profile without deleting it. |

Truth values also accept `yes/no`, `true/false`, `1/0`, `an/aus` and
`ja/nein`. Unknown keys and unreadable values are ignored. The shipped
examples cover terminals, IDEs and mail programs. On Linux under Wayland the
active application is known only on Hyprland and Sway; on GNOME and KDE the
normal settings apply. Window titles are never read.

## `server.env`

Written by the installer and by Quassel itself (atomically, mode 0600). It
configures the local speech server:

| Key | Meaning |
|---|---|
| `SERVER_BIN` | Path to the `whisper-server` binary. |
| `MODEL_PATH` | The selected `ggml-<model>.bin`. Change it with the control center or `model use`. |
| `WHISPER_THREADS` | Threads for the server (the installer picks up to 8). |
| `WHISPER_DECODE` | Decoder flags. `-bs 1` (greedy search) with a GPU, `-nf` on CPU. A measured value; do not raise it without a new measurement. |
| `VAD_MODEL` | Optional Silero model for the server's own voice detection (`ggml-silero-v5.1.2.bin`). |
| `REQUEST_PATH` | Optional random URL prefix (`/q/...`) so other local programs cannot guess the server address. Quassel generates one on macOS and Windows when the server starts; the Linux installer does not set one, but the systemd unit and the client honour it if you add it. |

## Text files

| File | Format |
|---|---|
| `dictionary.txt` | Your own words, one word or name per line. Biases recognition toward these spellings; learned words are added after them. Quassel keeps the Whisper prompt within the model's 224-token limit, so a very long dictionary is cut. |
| `replacements.txt` | `trigger=expansion`, one per line. `#` starts a comment. Matching ignores case; a later duplicate trigger wins. |
| `cleanup-corrections.txt` | `spoken=written`, one per line, each side at most 100 characters. Applied by the clean-up path with word boundaries, in one pass, so one correction cannot trigger another. |
| `ai_modes.txt` | `name=instruction`, one per line. The name also works as a spoken phrase ("tweet: ..."). |
| `dictionary-learned.json` | Auto-learned words with first and last sighting, count, and the list of removed words. Managed by Quassel; use the control center or `dict learned` / `dict remove`. |

## Environment variables

| Variable | Effect |
|---|---|
| `QUASSEL_PROFILE` | Names a second, independent instance (`QUASSEL_PROFILE=next`). It gets its own settings, data, runtime and log folders (`Quassel-next`, `quassel-next`) and its own server port. Models are shared, read-only. Names use `a-z`, `0-9` and `-`, up to 32 characters; an invalid name, or `QUASSEL_PORT=8765` together with a profile, stops the start with a message instead of falling back to the installed instance. Without it nothing changes. |
| `QUASSEL_PORT` | Port of the local speech server, 1024 to 65535. Default 8765; a named profile gets a fixed port between 8766 and 8865 derived from its name. |
| `QUASSEL_PREROLL_MS` | Pre-roll for recorders that were not given a value by the daemon (such as the control center's microphone test). The daemon itself uses `preroll_ms` in `config.ini`. |
| `QUASSEL_MAC_AUDIO` | macOS recording backend: `sd16` (default), `sdnative` or `ffmpeg`. For diagnosis only. |
| `QUASSEL_STATS_PATH` | Overrides the location of `stats.json` (used by tests). |
| `QUASSEL_BUNDLE` | Windows: folder with a bundled offline payload (engines and models), used instead of downloading. |
| `QUASSEL_CENTER_CMD` | Command the pill runs to open the control center (default `quassel-type`). |
