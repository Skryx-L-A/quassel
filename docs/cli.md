# Quassel command line and MCP server

`bin/quassel-type-cli` is the command-line client. It does not open a window,
load a model, or record from the microphone.

```sh
quassel-type status --json
quassel-type toggle
quassel-type panic
quassel-type transcribe recording.wav --json
quassel-type model list
quassel-type model use large-v3-turbo-q5_0
quassel-type dict add Quassel
quassel-type history list --since 2026-10-01T00:00:00Z
```

`status` reports the daemon, local speech server, selected model, active
profile, and latest state IPC value. `toggle` and `panic` use the existing
Linux `quassel-ctl` service mechanism; they exit with status 5 on platforms
without a daemon control channel.

`transcribe` accepts an existing supported media file. Plain text is the
default. `--json` includes text and segments. The current backend does not
expose timestamps, so `--srt` and `--vtt` emit plain text and warn on stderr
instead of inventing timings. Missing speech server, ffmpeg, or unsupported
file exits 1. Invalid commands exit 2; unavailable optional functions exit 5.
Command results go to stdout; diagnostics go to stderr.

`model use` selects an installed model through the existing atomic `server.env`
writer. It never downloads by default; pass `--download` only to fetch a
missing model. `dict` uses the atomic dictionary writer. `history list` only
reads history when normal Quassel history is enabled.

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
