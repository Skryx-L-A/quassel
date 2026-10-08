"""Gemeinsame Test-Fixtures.

Per-App-Profile (quassel/profiles.py) liest sonst die echte profiles.ini des
Nutzers. Legt jemand eine an, fragten alle Diktat-Tests plötzlich die echte
aktive Anwendung ab und verhielten sich je nach Fenster anders. Darum zeigt
PROFILES in jedem Test auf eine Datei, die es nicht gibt. Tests, die Profile
brauchen, setzen PROFILES selbst (monkeypatch überschreibt diesen Wert).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def _no_profiles_path(tmp_path_factory):
    return str(tmp_path_factory.mktemp("no-profiles") / "profiles.ini")


@pytest.fixture(autouse=True)
def _isolated_profiles(monkeypatch, _no_profiles_path):
    from quassel import profiles
    monkeypatch.setattr(profiles, "PROFILES", _no_profiles_path)
    profiles._cache["key"] = None
    yield
    profiles._cache["key"] = None
