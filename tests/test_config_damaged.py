"""Eine beschädigte config.ini wird nie still durch ihren lesbaren Teil ersetzt
(Review der Nacht 2026-10-09, Punkt 9)."""
import glob
import os

from quassel import config

DAMAGED = "[ui]\nlanguage = de\n[ui]\nlanguage = en\n[ai]\nendpoint = http://127.0.0.1:11434\n"


def _setup(tmp_path, monkeypatch, text):
    path = tmp_path / "config.ini"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG", str(path))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    return path


def test_migration_leaves_a_damaged_file_untouched(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch, DAMAGED)
    assert config.migrate() is False
    assert path.read_text(encoding="utf-8") == DAMAGED


def test_saving_over_a_damaged_file_keeps_a_copy(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, DAMAGED)
    config.save({("ui", "pill_preview"): "false"})
    copies = glob.glob(str(tmp_path / "config.ini.damaged-*"))
    assert len(copies) == 1
    assert open(copies[0], encoding="utf-8").read() == DAMAGED
    if os.name != "nt":
        assert os.stat(copies[0]).st_mode & 0o777 == 0o600


def test_migration_still_marks_a_healthy_file(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch, "[ui]\nlanguage = de\n")
    assert config.migrate() is True
    assert "[meta]" in path.read_text(encoding="utf-8")
    assert not glob.glob(str(tmp_path / "config.ini.damaged-*"))


def test_no_config_file_is_created_by_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    assert config.migrate() is False
    assert not (tmp_path / "config.ini").exists()
