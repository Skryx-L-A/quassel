"""A local, dependency-free MCP server for safe Quassel file operations.

The server uses JSON-RPC lines over stdio. It intentionally has no recording
tool: starting the microphone remains a direct action in the Quassel app.
"""
import json
import os
import sys

from . import cli, config

PROTOCOL_VERSION = "2025-06-18"


def _history_allowed():
    """An absent or malformed setting is privacy-preserving: no history."""
    parser = config._read_parser()
    try:
        return parser.getboolean("mcp", "mcp_history", fallback=False)
    except ValueError:
        return False


def _tool(name, description, properties=None, required=()):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": list(required), "additionalProperties": False}}


def tools():
    out = [
        _tool("transcribe_file", "Transcribe an existing local audio or video file.",
              {"path": {"type": "string", "description": "Local file path."}}, ("path",)),
        _tool("dictionary_list", "List local dictionary entries."),
        _tool("dictionary_add", "Add one local dictionary entry.",
              {"word": {"type": "string", "description": "Word or phrase."}}, ("word",)),
        _tool("status", "Show local daemon, speech server, model, and profile status."),
    ]
    if _history_allowed():
        out.append(_tool("history_list", "Read explicitly shared local dictation history.",
                         {"since": {"type": "string", "description": "ISO-8601 or Unix seconds."}}))
    return out


def _content(value, error=False):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
            "isError": error}


def _call(name, arguments):
    if name == "status":
        return _content(cli.status_data())
    if name == "dictionary_list":
        return _content({"words": config.dictionary_words()})
    if name == "dictionary_add":
        word = str(arguments.get("word", "")).strip()
        if not word:
            return _content({"error": "word must not be empty"}, True)
        words = config.dictionary_words()
        if word not in words:
            words.append(word)
            config.dictionary_save("\n".join(words))
        return _content({"word": word, "added": True})
    if name == "transcribe_file":
        path = arguments.get("path")
        if not isinstance(path, str) or not os.path.isfile(path):
            return _content({"error": "path must name an existing local file"}, True)
        from .transcribe_file import transcribe_file
        text = transcribe_file(path, config.Cfg())
        if text is None:
            return _content({"error": "transcription failed"}, True)
        return _content({"text": text, "segments": [],
                         "warning": "timestamps are unavailable from the current backend"})
    if name == "history_list":
        if not _history_allowed():
            return _content({"error": "history sharing is disabled"}, True)
        try:
            since = cli._history_since(arguments.get("since"))
        except Exception as exc:
            return _content({"error": str(exc)}, True)
        return _content({"entries": [entry for entry in config.history_read()
                                      if entry.get("ts", 0) >= since]})
    return _content({"error": f"unknown tool: {name}"}, True)


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def handle(message):
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid request")
    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(request_id, -32600, "method is required")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"protocolVersion": PROTOCOL_VERSION,
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "quassel", "version": "0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools()}}
    if method == "tools/call":
        params = message.get("params", {})
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(request_id, -32602, "tools/call requires a tool name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "tool arguments must be an object")
        return {"jsonrpc": "2.0", "id": request_id,
                "result": _call(params["name"], arguments)}
    if request_id is None:
        return None
    return _error(request_id, -32601, "method not found")


def serve(stdin=sys.stdin, stdout=sys.stdout):
    for raw in stdin:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            reply = _error(None, -32700, "parse error")
        else:
            reply = handle(message)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main():
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
