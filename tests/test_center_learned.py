"""Kontrollzentrum: gelernte Wörter sehen, behalten, entfernen."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from quassel import center, config  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for name, file in (("CONFIG", "config.ini"), ("DICTIONARY", "dictionary.txt"),
                       ("LEARNED", "dictionary-learned.json"),
                       ("REPLACEMENTS", "replacements.txt"),
                       ("CLEANUP_CORRECTIONS", "cleanup-corrections.txt")):
        monkeypatch.setattr(config, name, str(tmp_path / file))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    config._dictionary_cache["key"] = None
    config._learned_cache["key"] = None
    config.save({("system", "onboarded"): "true"})
    config.dictionary_save("Eigenname")
    now = config._now()
    config.dictionary_learn("PyTorch und NASA", now=now - 7200)
    config.dictionary_learn("PyTorch und NASA", now=now - 3600)


def _rows(c):
    rows = [c.learned_list.item(i).data(0x0100) for i in range(c.learned_list.count())]
    return [row for row in rows if row]          # Platzhalter "Keine gelernten Wörter"


def test_own_field_shows_only_own_words_and_list_shows_learned(qapp, cfg):
    c = center.Center()
    try:
        assert c.dict_edit.toPlainText() == "Eigenname"
        assert _rows(c) == ["PyTorch", "NASA"]
        assert "PyTorch" in c.learned_list.item(0).text()
        assert "Eigenname" in c.dict_effective.text() and "NASA" in c.dict_effective.text()
        assert c.learned_keep.accessibleName() and c.learned_forget.accessibleName()
    finally:
        c.close()


def test_forget_blocks_and_keep_moves(qapp, cfg):
    c = center.Center()
    try:
        c.learned_list.setCurrentRow(0)
        c.learned_forget.click()
        assert _rows(c) == ["NASA"]
        c.learned_list.setCurrentRow(0)
        c.learned_keep.click()
        assert _rows(c) == []
        assert c.dict_edit.toPlainText() == "Eigenname\nNASA"
    finally:
        c.close()
    assert config.dictionary_manual() == ["Eigenname", "NASA"]
    assert config.dictionary_learn("PyTorch", now=config._now() + 86400) == []
