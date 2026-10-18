"""os.replace in quassel/ läuft über fsutil.replace, damit kurze Windows-Sperren wiederholt werden."""
import os
import pathlib
import re

from quassel import config, fsutil, server_mac

QUASSEL = pathlib.Path(__file__).resolve().parents[1] / "quassel"

DIRECT_REPLACE_OUTSIDE_FSUTIL = set()


def test_only_fsutil_calls_os_replace():
    direct = set()
    for path in QUASSEL.rglob("*.py"):
        if re.search(r"\bos\.replace\(", path.read_text(encoding="utf-8")):
            direct.add(path.relative_to(QUASSEL).as_posix())
    direct.discard("fsutil.py")
    assert direct == DIRECT_REPLACE_OUTSIDE_FSUTIL


def test_log_rotation_retries_one_windows_lock(monkeypatch, tmp_path):
    log = tmp_path / "whisper-server.log"
    monkeypatch.setattr(config, "DATADIR", str(tmp_path))
    monkeypatch.setattr(server_mac, "LOG_FILE", str(log))
    log.write_bytes(b"x" * (1024 * 1024))
    (tmp_path / "whisper-server.log.1").write_bytes(b"alt")
    real_replace = os.replace
    locked = []

    def replace_once_locked(src, dst):
        if not locked:
            locked.append(src)
            raise PermissionError(32, "Datei von anderem Prozess belegt")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_once_locked)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(fsutil.time, "sleep", lambda seconds: None)
    server_mac._log_stream().close()
    assert locked == [str(log) + ".1"]
    assert (tmp_path / "whisper-server.log.2").read_bytes() == b"alt"
    assert (tmp_path / "whisper-server.log.1").stat().st_size == 1024 * 1024
