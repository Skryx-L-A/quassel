"""Diagnosebericht (quassel/diagnostics.py): Inhalt, Schwärzung, Ausschluss von
Verlauf/Wörterbuch, Logfilter, Linux-journalctl mit Attrappe und Frist. Alles
offline: kein Server, kein echtes Log, keine echte Konfiguration."""
import os
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import config, diagnostics, profiles, whisperclient  # noqa: E402

HISTORY_SECRET = "Das ist mein geheimes Diktat aus dem Verlauf"
DICT_WORD = "Zwiebelkuchenrezeptwort"
REPLACE_RULE = "mfg => Mit freundlichen Grüßen von Ersetzung"
AI_MODE_TEXT = "Schreibe das als Gedicht im Stil von KI-Modus-Text"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Eigenes Konfigurations-/Datenverzeichnis mit allen privaten Dateien."""
    home = tmp_path / "home" / "alice"
    conf = home / ".config" / "quassel"
    data = home / ".local" / "share" / "quassel"
    conf.mkdir(parents=True)
    data.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("QUASSEL_PROFILE", raising=False)
    monkeypatch.delenv("QUASSEL_PORT", raising=False)
    for name, path in (("CONFDIR", conf), ("DATADIR", data)):
        monkeypatch.setattr(config, name, str(path))
    files = {
        "CONFIG": conf / "config.ini",
        "SERVERENV": conf / "server.env",
        "DICTIONARY": conf / "dictionary.txt",
        "REPLACEMENTS": conf / "replacements.txt",
        "AI_MODES": conf / "ai_modes.txt",
        "HISTORY": data / "history.jsonl",
    }
    for name, path in files.items():
        monkeypatch.setattr(config, name, str(path))
    files["CONFIG"].write_text(
        "[speech]\nlanguage = de\nmic = USB Mic\n"
        "[ai]\nendpoint = http://bob:hunter2@127.0.0.1:11434\napi_key = sk-live-123456\n"
        "[cleanup]\nauth_token = abcdefgh\n", encoding="utf-8")
    files["SERVERENV"].write_text(
        "SERVER_BIN=%s/bin/whisper-server\n"
        "MODEL_PATH=%s/models/ggml-small.bin\n"
        "REQUEST_PATH=/q-7f3a9c1e2b\n"
        "WHISPER_DECODE=-bs 1\n" % (home, home), encoding="utf-8")
    files["DICTIONARY"].write_text(DICT_WORD + "\n", encoding="utf-8")
    files["REPLACEMENTS"].write_text(REPLACE_RULE + "\n", encoding="utf-8")
    files["AI_MODES"].write_text("gedicht: " + AI_MODE_TEXT + "\n", encoding="utf-8")
    files["HISTORY"].write_text('{"text": "%s"}\n' % HISTORY_SECRET, encoding="utf-8")
    prof = conf / "profiles.ini"
    prof.write_text("[profile:Terminal]\nmatch = secretapp-x, kitty\nlanguage = en\n"
                    "[profile:Mail]\nmatch = *mail*\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "PROFILES", str(prof))
    log = tmp_path / "daemon.log"
    log.write_text("10:00:00 quassel-daemon 2.7.0 gestartet\n"
                   "10:00:01 dict: bt=False vad=True rec=3.10s\n"
                   "10:00:02 hyp=hallo welt\n"
                   "10:00:03 server url http://127.0.0.1:8765/q-7f3a9c1e2b/inference\n",
                   encoding="utf-8")
    monkeypatch.setattr(diagnostics, "daemon_log_file", lambda: str(log))
    monkeypatch.setattr(whisperclient, "server_up", lambda timeout=2: True)
    return {"home": str(home), "log": log, **files}


def test_report_contains_the_expected_parts(env):
    text = diagnostics.build_report()
    for heading in ("== Version ==", "== Instance ==", "== Model ==",
                    "== Settings (config.ini) ==", "== Server environment (server.env) ==",
                    "== Server ==", "== Per-app profiles", "== Daemon log"):
        assert heading in text
    assert "quassel: " in text
    assert "profile: default" in text
    assert "port: 8765" in text
    assert "model: ggml-small.bin" in text
    assert "reachable: yes" in text
    assert "language = de" in text
    assert "WHISPER_DECODE=-bs 1" in text
    assert "- Terminal" in text and "- Mail" in text
    assert "quassel-daemon 2.7.0 gestartet" in text


def test_secrets_are_redacted(env):
    text = diagnostics.build_report()
    for secret in ("sk-live-123456", "abcdefgh", "hunter2", "q-7f3a9c1e2b"):
        assert secret not in text
    assert "api_key = <entfernt>" in text
    assert "auth_token = <entfernt>" in text
    assert "REQUEST_PATH=<entfernt>" in text
    assert "http://<entfernt>@127.0.0.1:11434" in text


def test_home_is_replaced_by_tilde(env):
    text = diagnostics.build_report()
    assert env["home"] not in text
    assert "alice" not in text
    assert "SERVER_BIN=~/bin/whisper-server" in text


def test_private_files_never_appear(env):
    text = diagnostics.build_report()
    for private in (HISTORY_SECRET, DICT_WORD, REPLACE_RULE, AI_MODE_TEXT):
        assert private not in text
    # profiles.ini: nur Namen, keine Muster oder Einstellungen
    assert "secretapp-x" not in text and "*mail*" not in text


def test_private_files_are_not_even_opened(env, monkeypatch):
    real_open = open
    private = {os.path.realpath(env[k]) for k in
               ("HISTORY", "DICTIONARY", "REPLACEMENTS", "AI_MODES")}
    opened = []

    def spy(path, *a, **kw):
        if isinstance(path, (str, os.PathLike)) and os.path.realpath(path) in private:
            opened.append(path)
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", spy)
    diagnostics.build_report()
    assert opened == []


def test_log_lines_that_look_like_text_are_removed(env):
    text = diagnostics.build_report()
    assert "hallo welt" not in text
    assert diagnostics.LINE_REMOVED in text
    assert "removed as possibly dictated text: 1" in text


@pytest.mark.parametrize("line, kept", [
    ("10:00 dict: bt=False vad=True rec=3.10s", True),
    ("10:00 wake: heard 12 chars, match=yes", True),
    ("10:00 hotkey: chord down", True),
    ("10:00 rescue: '/tmp/quassel/rescue-1.wav' nicht schreibbar", True),
    ("10:00 text=Hallo", False),
    ("10:00 hyp: guten morgen", False),
    ("10:00 got 'das ist ein ziemlich langer satz'", False),
    ("10:00 got „das ist ein ziemlich langer satz“", False),
    ("10:00 short 'kurz ok'", True),
])
def test_text_filter(line, kept):
    out, removed = diagnostics.filter_log([line])
    assert (out == [line]) is kept
    assert removed == (0 if kept else 1)


@pytest.mark.parametrize("line, secret", [
    ("token=abc123", "abc123"),
    ("api_key: sk-999", "sk-999"),
    ("X-Auth-Token: zzzz", "zzzz"),
    ("password=hunter2", "hunter2"),
    ("REQUEST_PATH=/q-1", "/q-1"),
    ("Authorization: Bearer eyJhbGciOi", "eyJhbGciOi"),
    ("GET http://user:pw@host/x", "user:pw"),
])
def test_log_secret_redaction(line, secret):
    out, _ = diagnostics.filter_log([line])
    assert secret not in out[0]
    assert diagnostics.REDACTED in out[0]


def test_failing_part_is_marked_and_report_continues(env):
    def boom():
        raise PermissionError("no access")
    parts = (("Broken", boom),) + diagnostics.PARTS
    text = diagnostics.build_report(parts=parts)
    assert "== Broken ==\n<not collected: PermissionError: no access>" in text
    assert "== Version ==" in text and "== Daemon log" in text


def test_unreadable_config_does_not_stop_report(env):
    env["CONFIG"].write_text("api_key = sk-broken-999\n[ai]\n", encoding="utf-8")
    text = diagnostics.build_report()
    assert "<not collected: MissingSectionHeaderError>" in text
    assert "sk-broken-999" not in text      # configparser zitiert die Zeile
    assert "== Server ==" in text


def test_secret_in_endpoint_query_is_redacted(env):
    env["CONFIG"].write_text("[ai]\nendpoint = https://h.example/v1?api_key=qq-777\n",
                             encoding="utf-8")
    text = diagnostics.build_report()
    assert "qq-777" not in text
    assert "endpoint = https://h.example/v1?api_key=<entfernt>" in text


def test_server_down_is_reported(env, monkeypatch):
    calls = []
    monkeypatch.setattr(whisperclient, "server_up",
                        lambda timeout=2: calls.append(timeout) or False)
    text = diagnostics.build_report()
    assert "reachable: no" in text
    assert calls == [diagnostics.SERVER_TIMEOUT]


def test_missing_log_file(env, tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "daemon_log_file", lambda: str(tmp_path / "nope.log"))
    assert "(no log file yet)" in diagnostics.build_report()


def test_tail_file_returns_last_lines(tmp_path):
    p = tmp_path / "big.log"
    p.write_text("".join("line %d\n" % i for i in range(1000)), encoding="utf-8")
    lines = diagnostics.tail_file(str(p), n=200)
    assert len(lines) == 200
    assert lines[0] == "line 800" and lines[-1] == "line 999"


def test_log_paths_per_platform(monkeypatch):
    monkeypatch.delenv("QUASSEL_PROFILE", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert diagnostics.daemon_log_file().endswith(
        os.path.join("Library", "Logs", "Quassel", "daemon.log"))
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    assert os.path.join("Logs", "Quassel-next", "daemon.log") in diagnostics.daemon_log_file()
    monkeypatch.setattr(sys, "platform", "linux")
    if os.name != "nt":
        assert diagnostics.daemon_log_file() is None


# ------------------------------------------------------------ Linux journalctl
def test_journalctl_command_and_deadline(env, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout=(
            "2026-10-09T05:00:00+0200 box quasseld[1]: dict: rec=1.0s\n"
            "2026-10-09T05:00:01+0200 box quasseld[1]: text=geheim\n"), stderr="")

    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/journalctl")
    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    monkeypatch.setattr(diagnostics, "daemon_log_file", lambda: None)
    monkeypatch.setenv("QUASSEL_PROFILE", "next")
    text = diagnostics.build_report()
    assert seen["cmd"] == ["/usr/bin/journalctl", "--user", "-u", "quasseld@next.service",
                           "-n", "200", "--no-pager", "-o", "short-iso"]
    assert seen["kw"]["timeout"] == diagnostics.JOURNAL_TIMEOUT
    assert "dict: rec=1.0s" in text
    assert "geheim" not in text


def test_journalctl_timeout_is_marked(env, monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/journalctl")
    monkeypatch.setattr(diagnostics.subprocess, "run", slow)
    monkeypatch.setattr(diagnostics, "daemon_log_file", lambda: None)
    text = diagnostics.build_report()
    assert "<not collected: timed out>" in text
    assert "== Version ==" in text


def test_journalctl_missing_is_marked(env, monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
    monkeypatch.setattr(diagnostics, "daemon_log_file", lambda: None)
    text = diagnostics.build_report()
    assert "<not collected: FileNotFoundError: journalctl not found>" in text


# ------------------------------------------------------------ Datei speichern
@pytest.mark.skipif(os.name == "nt", reason="POSIX-Dateirechte")
def test_save_report_is_private(tmp_path):
    path = tmp_path / "out.txt"
    diagnostics.save_report(str(path), "hello\n")
    assert path.read_text(encoding="utf-8") == "hello\n"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_default_filename():
    import datetime as dt
    assert diagnostics.default_filename(dt.datetime(2026, 10, 9, 5, 30, 1)) == \
        "quassel-diagnostics-20261009-053001.txt"
