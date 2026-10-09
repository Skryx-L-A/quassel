"""Modellauswahl im Kontrollzentrum (offscreen, eigene config.ini, server.env
nur als Attrappe): ein selbst eingetragenes Modell erscheint mit seinem
Dateinamen und wird nie stillschweigend gegen "small" getauscht (B42)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication              # noqa: E402

from quassel import center, config, i18n               # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    config.save({("ui", "language"): "en", ("system", "onboarded"): "true"})
    writes, restarts = [], []
    monkeypatch.setattr(config, "write_serverenv", writes.append)
    monkeypatch.setattr(center, "restart_server", lambda *a: restarts.append(a))
    wins = []

    def build(model_path):
        monkeypatch.setattr(config, "read_serverenv",
                            lambda: {"MODEL_PATH": model_path} if model_path else {})
        win = center.Center()
        wins.append(win)
        return win, writes, restarts
    yield build
    for w in wins:
        w.close()
    i18n.set_language(None)


def test_known_model_is_selected_by_its_file_name(make, tmp_path):
    win, _, _ = make(str(tmp_path / "ggml-large-v3-turbo-q5_0.bin"))
    assert win.model.currentData() == "large-v3-turbo-q5_0"
    assert win.model.count() == len(config.MODELS)


def test_unknown_model_shows_its_file_name_not_small(make, tmp_path):
    win, writes, restarts = make(str(tmp_path / "ggml-my-finetune-de.bin"))
    assert win.model.currentText() == "ggml-my-finetune-de.bin"
    assert win.model.currentData() != "small"
    # Die bekannten Modelle bleiben waehlbar, das eigene steht obenan.
    assert win.model.count() == len(config.MODELS) + 1
    # Erneut das eigene Modell melden (z. B. durch Signal) aendert nichts.
    win.on_model()
    assert writes == [] and restarts == []


def test_choosing_the_own_model_again_switches_back_without_download(
        make, tmp_path, monkeypatch):
    own = str(tmp_path / "ggml-my-finetune-de.bin")
    small = tmp_path / "ggml-small.bin"
    small.write_bytes(b"x" * 2048)
    win, writes, restarts = make(own)
    monkeypatch.setattr(center, "net_download",
                        lambda *a: pytest.fail("no download for an own model"))
    win.model.setCurrentIndex(win.model.findData("small"))
    assert writes[-1]["MODEL_PATH"] == str(small)
    monkeypatch.setattr(config, "read_serverenv", lambda: {"MODEL_PATH": str(small)})
    win.model.setCurrentIndex(0)
    assert writes[-1]["MODEL_PATH"] == own
    assert len(restarts) == 2


def test_prefix_of_a_known_name_is_not_taken_for_it(make, tmp_path):
    # "ggml-small.bin" steckt nicht in "ggml-small.en.bin" -- aber auch kein
    # Teilstring-Treffer anderer Art darf ein fremdes Modell als bekanntes zeigen.
    win, _, _ = make(str(tmp_path / "xggml-small.bin"))
    assert win.model.currentText() == "xggml-small.bin"


def test_without_server_env_the_default_stays_small(make):
    win, _, _ = make("")
    assert win.model.currentData() == "small"
