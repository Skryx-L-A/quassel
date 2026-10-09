# Quassel command line and MCP server

The command-line client does not open a window, load a model, or record from
the microphone. It is not installed by the installers yet; run it from a
checkout of the repository:

```sh
bin/quassel-type-cli status --json      # picks the project's .venv if present
python3 -m quassel status --json        # the same, from the repository root
```

An installed Linux copy keeps the package in `~/.local/lib/quassel`, so
`PYTHONPATH=~/.local/lib/quassel python3 -m quassel ...` works there. Do not
confuse it with `quassel-type`, the command that installs on Linux as the
launcher of the control center window. The examples below use the checkout
script.

```sh
bin/quassel-type-cli status --json
bin/quassel-type-cli toggle
bin/quassel-type-cli panic
bin/quassel-type-cli raw-last
bin/quassel-type-cli transcribe recording.wav --json
bin/quassel-type-cli model list
bin/quassel-type-cli model use large-v3-turbo-q5_0
bin/quassel-type-cli dict add Quassel
bin/quassel-type-cli history list --since 2026-10-01T00:00:00Z
```

`status` reports the daemon, local speech server, selected model, active
profile, and latest state IPC value. `toggle` and `panic` use the existing
Linux `quassel-ctl` service mechanism; they exit with status 5 on platforms
without a daemon control channel.

`raw-last` asks the running Linux daemon (SIGHUP) to put the unedited text of
the last dictation on the clipboard. The command never reads the text; it lives
only in the daemon's memory. The pill shows whether there was any raw text.
On macOS and Windows use "Raw text back" in the menu bar or tray menu; the
command exits 5 there.

With `QUASSEL_PROFILE` set (a second instance next to the installed one), the
daemon status and `raw-last` use that profile's service
`quasseld@<profile>.service` (template `systemd/quasseld@.service`) instead of
`quasseld.service`, so one instance never hands out the other's text.

`transcribe` accepts an existing supported media file. Plain text is the
default. `--json` includes text and segments; `--srt` and `--vtt` render the
timestamped segments returned by the local speech server. When an older server
does not provide segment timestamps, Quassel emits plain text and warns on
stderr instead of inventing timings. Long files are split into twelve-second
chunks; segment times are normalized to the source file. The underlying API
also accepts a progress callback and cancellation event. A missing speech server, a missing
`ffmpeg` for a file that is not 16-bit WAV, or an unsupported file exits 1. Invalid commands exit 2; unavailable
optional functions exit 5. Command results go to stdout; diagnostics go to
stderr.

`model use` selects an installed model through the existing atomic `server.env`
writer. It never downloads by default; pass `--download` only to fetch a
missing model. `dict` uses the atomic dictionary writer. `history list` only
reads history when normal Quassel history is enabled.

`profiles init|list|path` manages the per-app profile file `profiles.ini` (see
[CONFIG.md](CONFIG.md#profilesini-per-app-profiles-opt-in)): `init` writes the
shipped examples once and never overwrites an existing file, `list` shows the
parsed profiles, `path` prints the location.

## MCP

The local MCP server communicates only over stdio and has no third-party
runtime dependency. It offers `transcribe_file`, `dictionary_list`,
`dictionary_add`, and `status`. It deliberately has no microphone or recording
tool. `transcribe_file` only reads the path supplied by the MCP client.

MCP history is off by default. To explicitly share it with the local MCP
client, add this to Quassel's `config.ini`:

```ini
[mcp]
mcp_history = true
```

Only then does `history_list` appear in `tools/list`.

For Claude Code from a checkout:

```sh
claude mcp add --transport stdio quassel -- /absolute/path/to/VoxType/.venv/bin/python -m quassel.mcp_server
```

For Cursor, add this equivalent command to its MCP configuration:

```json
{"mcpServers":{"quassel":{"command":"/absolute/path/to/VoxType/.venv/bin/python","args":["-m","quassel.mcp_server"]}}}
```

The server implements JSON-RPC 2.0 with MCP protocol version `2025-06-18`:
`initialize`, `tools/list`, and `tools/call`.
