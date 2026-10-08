"""Offline checks for server diagnostics; no server or model is started."""
import io
import os

from quassel import server_mac


def test_mac_log_rotates_while_streaming(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mac.config, "DATADIR", str(tmp_path))
    monkeypatch.setattr(server_mac, "LOG_FILE", str(tmp_path / "server.log"))
    (tmp_path / "server.log").write_bytes(b"a" * (1024 * 1024 - 1))
    server_mac._drain_log(io.BytesIO(b"bc"))
    assert (tmp_path / "server.log").read_bytes() == b"bc"
    assert os.path.getsize(tmp_path / "server.log.1") == 1024 * 1024 - 1


def test_mac_args_do_not_enable_transcript_printing():
    args = server_mac.build_args({"SERVER_BIN": "/offline/server", "MODEL_PATH": "/offline/model"})
    assert "-pp" not in args and "--print-progress" not in args
