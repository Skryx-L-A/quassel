"""pyproject.toml nennt die Lizenz des Projekts, dieselbe wie in LICENSE."""
import pathlib

import pytest

tomllib = pytest.importorskip("tomllib")  # ab Python 3.11; die CI-Matrix hat auch 3.10

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_pyproject_names_the_project_license():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["license"] == "GPL-3.0-or-later"
    assert "GNU GENERAL PUBLIC LICENSE" in (ROOT / "LICENSE").read_text(encoding="utf-8")
