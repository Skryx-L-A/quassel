"""A local, dependency-free MCP server for safe Quassel file operations.

The server uses JSON-RPC lines over stdio. It intentionally has no recording
tool: starting the microphone remains a direct action in the Quassel app.
The opt-in tool ask_user only waits for the next dictation the user starts
themselves (quassel/ask.py).
"""
import json
import os
import sys
import threading

from . import cli, config

PROTOCOL_VERSION = "2025-06-18"
# Name the MCP client gave in initialize (clientInfo.name): the pill and the
# notification say who asks.
_client = {"name": ""}


def _history_allowed():
    """An absent or malformed setting is privacy-preserving: no history.
    Both switches must be on: sharing over MCP and the history itself. With
    the history switched off, entries still on disk must not leave over MCP
    (the CLI refuses the same)."""
    parser = config._read_parser()
    try:
        shared = parser.getboolean("mcp", "mcp_history", fallback=False)
    except ValueError:
        return False
    return shared and config.Cfg().history_enabled


def _ask_allowed():
    """ask_user is off unless [mcp] mcp_ask = true: a program that may wait
    for your next dictation is a choice, not a default. An absent or
    malformed setting keeps it off."""
    parser = config._read_parser()
    try:
        return parser.getboolean("mcp", "mcp_ask", fallback=False)
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
    if _ask_allowed():
        out.append(_tool(
            "ask_user",
            "Ask the user a question and wait for a spoken answer. Quassel shows the "
            "question; the user answers with their next dictation, which they start "
            "themselves with the hotkey. This never starts the microphone. Returns the "
            "transcribed answer, or an error if nobody answered before the timeout.",
            {"question": {"type": "string", "description": "Short question shown to the user."},
             "timeout": {"type": "number", "minimum": 1, "maximum": 600,
                         "description": "Seconds to wait for the user (default 120, max 600)."}},
            ("question",)))
    return out


def _content(value, error=False):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
            "isError": error}


def _ask_user(arguments, cancel=None):
    from . import ask, i18n
    if not _ask_allowed():
        return _content({"error": "ask_user is disabled; set [mcp] mcp_ask = true"}, True)
    question = arguments.get("question")
    if not isinstance(question, str) or not question.strip():
        return _content({"error": "question must be a non-empty string"}, True)
    timeout = arguments.get("timeout", ask.DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
            or not 0 < timeout <= ask.MAX_TIMEOUT:
        return _content({"error": "timeout must be a number of seconds from 1 to 600"}, True)
    if not cli._daemon_answers():
        return _content({"error": "Quassel is not running", "reason": "unavailable"}, True)
    try:
        cfg = config.Cfg()
        i18n.set_language(None if cfg.ui_language == "auto" else cfg.ui_language)
    except Exception:   # noqa: BLE001 — only the language of the notification
        pass
    try:
        answer = ask.ask(question, timeout, _client["name"] or "MCP", notify=ask.notify_user,
                         stop=cancel.is_set if cancel is not None else None)
    except ask.AskError as exc:
        return _content({"error": str(exc), "reason": exc.reason}, True)
    return _content({"answer": answer})


def _call(name, arguments, cancel=None):
    if name == "ask_user":
        return _ask_user(arguments, cancel)
    if name == "status":
        return _content(cli.status_data())
    if name == "dictionary_list":
        return _content({"words": config.dictionary_words()})
    if name == "dictionary_add":
        word = str(arguments.get("word", "")).strip()
        if not word:
            return _content({"error": "word must not be empty"}, True)
        words = config.dictionary_manual()
        if word not in words:
            words.append(word)
            config.dictionary_save("\n".join(words))
        return _content({"word": word, "added": True})
    if name == "transcribe_file":
        path = arguments.get("path")
        if not isinstance(path, str) or not os.path.isfile(path):
            return _content({"error": "path must name an existing local file"}, True)
        from .transcribe_file import transcribe_file
        transcript = transcribe_file(path, config.Cfg(), output_format="verbose_json")
        if transcript is None:
            return _content({"error": "transcription failed"}, True)
        result = {"text": transcript.text, "segments": list(transcript.segments)}
        if not transcript.segments:
            result["warning"] = "timestamps are unavailable from the current backend"
        return _content(result)
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


def handle(message, cancel=None):
    """One JSON-RPC message. cancel: threading.Event that ends a waiting
    ask_user early (see serve)."""
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid request")
    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(request_id, -32600, "method is required")
    if method == "initialize":
        params = message.get("params")
        info = params.get("clientInfo") if isinstance(params, dict) else None
        name = info.get("name") if isinstance(info, dict) else None
        _client["name"] = name if isinstance(name, str) else ""
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
                "result": _call(params["name"], arguments, cancel)}
    if request_id is None:
        return None
    return _error(request_id, -32601, "method not found")


def _id_key(value):
    """A JSON-RPC id as dictionary key; ids that are no string or number
    (malformed input) cannot be canceled."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return value


def _is_ask_call(message):
    if not isinstance(message, dict):
        return False
    params = message.get("params")
    return (message.get("method") == "tools/call" and isinstance(params, dict)
            and params.get("name") == "ask_user")


def serve(stdin=sys.stdin, stdout=sys.stdout):
    """Read requests until EOF. ask_user waits up to minutes for the user, so
    it runs on its own thread while this loop keeps reading: a client that
    cancels the call (notifications/cancelled) or closes stdin ends the
    question at once. Otherwise the request would stay open and take the
    user's next dictation, with nobody left to read the answer."""
    lock = threading.Lock()
    waiting = {}            # JSON-RPC id of a running ask_user -> its cancel event
    threads = []

    def write(reply):
        if reply is None:
            return
        with lock:
            try:
                stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
                stdout.flush()
            except (OSError, ValueError):
                pass            # client gone; the request is closed regardless

    def run_ask(message, cancel):
        try:
            reply = handle(message, cancel)
        finally:
            with lock:
                waiting.pop(_id_key(message.get("id")), None)
        if not cancel.is_set():         # a canceled call gets no response (MCP)
            write(reply)

    try:
        for raw in stdin:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                write(_error(None, -32700, "parse error"))
                continue
            if isinstance(message, dict) and message.get("method") == "notifications/cancelled":
                params = message.get("params")
                request_id = _id_key(params.get("requestId")) if isinstance(params, dict) else None
                with lock:
                    cancel = waiting.get(request_id) if request_id is not None else None
                if cancel is not None:
                    cancel.set()
                continue
            if _is_ask_call(message):
                cancel = threading.Event()
                key = _id_key(message.get("id"))
                if key is not None:
                    with lock:
                        waiting[key] = cancel
                thread = threading.Thread(target=run_ask, args=(message, cancel),
                                          name="mcp-ask_user", daemon=True)
                thread.cancel = cancel
                threads.append(thread)
                thread.start()
                continue
            write(handle(message))
    finally:
        # EOF (or a broken stdin): nobody reads a later answer.
        for thread in threads:
            thread.cancel.set()
        for thread in threads:
            thread.join(5)
    return 0


def main():
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
