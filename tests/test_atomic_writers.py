"""Alle Dateien, die Quassel schreibt, werden atomar ersetzt (Audit B30): ein
Absturz oder ein Fehler mitten im Schreiben darf nie eine leere oder halbe
Datei zurücklassen. Vorher wurde jede mit open(..., "w") sofort auf null
gekürzt und erst danach gefüllt."""
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import config, fsutil, stats


# ------------------------------------------------------------------ fsutil
def test_atomic_write_creates_directories_and_the_file(tmp_path):
    target = tmp_path / "a" / "b" / "x.txt"
    fsutil.atomic_write(str(target), "hallo äö\n")
    assert target.read_text(encoding="utf-8") == "hallo äö\n"
    assert [p.name for p in target.parent.iterdir()] == ["x.txt"]


def test_atomic_write_replaces_an_existing_file(tmp_path):
    target = tmp_path / "x.txt"
    target.write_text("alt", encoding="utf-8")
    fsutil.atomic_write(str(target), "neu")
    assert target.read_text(encoding="utf-8") == "neu"


def test_a_write_that_fails_midway_keeps_the_old_file(tmp_path):
    """Ein Zeichen, das sich nicht als UTF-8 schreiben lässt, bricht das
    Schreiben ab, NACHDEM die Datei geöffnet wurde: die Stelle, an der die alte
    Fassung bisher schon weg war."""
    target = tmp_path / "x.txt"
    target.write_text("alt", encoding="utf-8")
    with pytest.raises(UnicodeEncodeError):
        fsutil.atomic_write(str(target), "neu \udc80")
    assert target.read_text(encoding="utf-8") == "alt"
    assert [p.name for p in tmp_path.iterdir()] == ["x.txt"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX-Rechte")
def test_atomic_write_keeps_the_permissions_of_the_existing_file(tmp_path):
    target = tmp_path / "x.txt"
    target.write_text("alt", encoding="utf-8")
    os.chmod(target, 0o640)
    fsutil.atomic_write(str(target), "neu")
    assert (os.stat(target).st_mode & 0o777) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX-Rechte")
def test_atomic_write_applies_an_explicit_mode_even_to_an_existing_file(tmp_path):
    target = tmp_path / "x.txt"
    target.write_text("alt", encoding="utf-8")
    os.chmod(target, 0o644)
    fsutil.atomic_write(str(target), "neu", mode=0o600)
    assert (os.stat(target).st_mode & 0o777) == 0o600
    fresh = tmp_path / "y.txt"
    fsutil.atomic_write(str(fresh), "neu", mode=0o600)
    assert (os.stat(fresh).st_mode & 0o777) == 0o600


def test_concurrent_writers_never_leave_a_mixed_file(tmp_path):
    """Zwei Schreiber (Daemon und Kontrollzentrum) benutzten dieselbe
    Temp-Datei und hätten sich gegenseitig durchmischt."""
    target = str(tmp_path / "x.txt")
    texts = ["A" * 5000, "B" * 5000]

    errors = []

    def work(text):
        try:
            for _ in range(40):
                fsutil.atomic_write(target, text)
        except BaseException as exc:      # noqa: BLE001 — ein toter Schreiber zählt
            errors.append(exc)
    threads = [threading.Thread(target=work, args=(t,)) for t in texts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with open(target, encoding="utf-8") as f:
        assert f.read() in texts
    assert [p.name for p in tmp_path.iterdir()] == ["x.txt"]


def test_replace_retries_a_short_windows_lock(tmp_path, monkeypatch):
    """Unter Windows lehnt os.replace mit PermissionError ab, solange ein anderer
    das Ziel gerade öffnet oder ersetzt; nach kurzer Pause geht es."""
    monkeypatch.setattr(fsutil.os, "name", "nt")
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) < 3:
            raise PermissionError(13, "Access is denied")
        real(src, dst)
    monkeypatch.setattr(fsutil.os, "replace", flaky)
    (tmp_path / "a.tmp").write_text("neu", encoding="utf-8")
    fsutil.replace(str(tmp_path / "a.tmp"), str(tmp_path / "a.txt"))
    assert len(calls) == 3
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "neu"


def test_replace_gives_up_on_a_lasting_lock_and_never_retries_on_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    calls = []

    def locked(src, dst):
        calls.append(src)
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(fsutil.os, "replace", locked)
    monkeypatch.setattr(fsutil.os, "name", "posix")
    with pytest.raises(PermissionError):
        fsutil.replace("a", "b")
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setattr(fsutil.os, "name", "nt")
    with pytest.raises(PermissionError):
        fsutil.replace("a", "b")
    assert len(calls) == fsutil._REPLACE_TRIES


# ---------------------------------------------- jede Datei, die Quassel schreibt
def _iso(tmp_path, monkeypatch):
    for name, fname in (("CONFIG", "config.ini"), ("DICTIONARY", "dictionary.txt"),
                        ("REPLACEMENTS", "replacements.txt"),
                        ("AI_MODES", "ai_modes.txt"), ("HISTORY", "history.jsonl"),
                        ("SERVERENV", "server.env")):
        monkeypatch.setattr(config, name, str(tmp_path / fname))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "DATADIR", str(tmp_path))
    monkeypatch.setenv("QUASSEL_STATS_PATH", str(tmp_path / "stats.json"))
    config._dictionary_cache["key"] = None


WRITERS = {
    "config.ini": (lambda: config.save({("hotkey", "chord"): "alt+meta"}),
                   lambda: config.save({("hotkey", "chord"): "ctrl+alt"})),
    "dictionary.txt": (lambda: config.dictionary_save("Alt"),
                       lambda: config.dictionary_save("Neu")),
    "replacements.txt": (lambda: config.replacement_save("a=b"),
                         lambda: config.replacement_save("c=d")),
    "ai_modes.txt": (lambda: config.ai_modes_save("x=alt"),
                     lambda: config.ai_modes_save("x=neu")),
    "history.jsonl": (lambda: config.history_append("alt"),
                      lambda: config.history_append("neu")),
    "server.env": (lambda: config.write_serverenv({"MODEL_PATH": "/alt"}),
                   lambda: config.write_serverenv({"MODEL_PATH": "/neu"})),
    "stats.json": (lambda: stats.record("alt alt"),
                   lambda: stats.record("neu neu neu")),
}


@pytest.mark.parametrize("fname", sorted(WRITERS))
def test_a_failed_replace_keeps_the_old_file(tmp_path, monkeypatch, fname):
    _iso(tmp_path, monkeypatch)
    first, second = WRITERS[fname]
    first()
    before = (tmp_path / fname).read_bytes()

    def boom(*a, **kw):
        raise OSError("Platte voll")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        second()
    monkeypatch.undo()
    assert (tmp_path / fname).read_bytes() == before
    assert not [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


@pytest.mark.parametrize("fname", sorted(WRITERS))
def test_the_result_is_complete_after_a_normal_write(tmp_path, monkeypatch, fname):
    _iso(tmp_path, monkeypatch)
    first, second = WRITERS[fname]
    first()
    second()
    data = (tmp_path / fname).read_text(encoding="utf-8")
    assert data.endswith("\n") or fname in ("config.ini", "stats.json")
    if fname == "stats.json":
        assert json.loads(data)["sessions"] == 2
    assert [p.name for p in tmp_path.iterdir()].count(fname) == 1
    assert not [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
