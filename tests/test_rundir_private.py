"""Der Laufzeitordner entsteht nur für den eigenen Nutzer lesbar (0700),
auch wenn XDG_RUNTIME_DIR nicht privat ist (Review 2026-10-09, UNBELEGT)."""
import os
import stat

import pytest

from quassel import state


@pytest.mark.skipif(os.name == "nt", reason="POSIX-Rechte")
def test_runtime_folder_is_created_private(tmp_path, monkeypatch):
    target = tmp_path / "quassel-test"
    monkeypatch.setattr(state, "RUNDIR", str(target))
    monkeypatch.setattr(state, "STATE", str(target / "state.json"))
    state.state_set("idle")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o700
