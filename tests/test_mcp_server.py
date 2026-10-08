"""JSON-RPC unit tests for the dependency-free local MCP server."""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import config, mcp_server


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "DATADIR", str(tmp_path))
    monkeypatch.setattr(config, "DICTIONARY", str(tmp_path / "dictionary.txt"))
    monkeypatch.setattr(config, "HISTORY", str(tmp_path / "history.jsonl"))


def request(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return mcp_server.handle(message)


def test_initialize_and_tools_list_advertise_the_safe_tools():
    initialized = request("initialize")
    assert initialized["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    tools = request("tools/list")["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "transcribe_file", "dictionary_list", "dictionary_add", "status"}


def test_dictionary_tool_round_trip_uses_config_storage():
    assert request("tools/call", {"name": "dictionary_add", "arguments": {"word": "Quassel"}})["result"]["isError"] is False
    result = request("tools/call", {"name": "dictionary_list", "arguments": {}})
    assert json.loads(result["result"]["content"][0]["text"])["words"] == ["Quassel"]


def test_transcribe_file_never_starts_a_microphone(monkeypatch, tmp_path):
    source = tmp_path / "sample.wav"
    source.write_bytes(b"test")
    calls = []
    monkeypatch.setattr("quassel.transcribe_file.transcribe_file", lambda path, cfg: calls.append(path) or "hello")
    result = request("tools/call", {"name": "transcribe_file", "arguments": {"path": str(source)}})
    assert result["result"]["isError"] is False
    assert calls == [str(source)]


def test_history_tool_is_hidden_until_explicitly_enabled(tmp_path):
    assert "history_list" not in {t["name"] for t in mcp_server.tools()}
    config.save({("mcp", "mcp_history"): "true"})
    assert "history_list" in {t["name"] for t in mcp_server.tools()}


def test_stdio_server_returns_parse_error_and_continues():
    stdin = io.StringIO("not json\n" + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "initialize"}) + "\n")
    stdout = io.StringIO()
    assert mcp_server.serve(stdin, stdout) == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["id"] == 3
