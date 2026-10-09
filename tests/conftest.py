"""Gemeinsame Test-Fixtures.

Per-App-Profile (quassel/profiles.py) liest sonst die echte profiles.ini des
Nutzers. Legt jemand eine an, fragten alle Diktat-Tests plötzlich die echte
aktive Anwendung ab und verhielten sich je nach Fenster anders. Darum zeigt
PROFILES in jedem Test auf eine Datei, die es nicht gibt. Tests, die Profile
brauchen, setzen PROFILES selbst (monkeypatch überschreibt diesen Wert).
"""
import atexit
import os
import shutil
import sys
import tempfile

import pytest

# Alle Pfade von quassel (Konfiguration, Wörterbuch, Verlauf, Laufzeitordner)
# werden beim Import aus dem Home berechnet. Ein Fixture, das nur einen davon
# umbiegt, lässt die anderen auf der echten Installation (so überschrieb ein
# Test das echte Wörterbuch). Darum zeigt das Home schon vor dem ersten Import
# von quassel auf einen Wegwerfordner; tests/test_isolation.py prüft das.
# Windows: expanduser liest USERPROFILE, nicht HOME; Quassel nutzt dort APPDATA.
os.environ.setdefault("QUASSEL_TEST_REAL_HOME", os.path.expanduser("~"))
_TEST_HOME = tempfile.mkdtemp(prefix="quassel-test-home-")
atexit.register(shutil.rmtree, _TEST_HOME, True)
os.environ["HOME"] = os.environ["USERPROFILE"] = _TEST_HOME
for _var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
             "APPDATA", "LOCALAPPDATA"):
    os.environ.pop(_var, None)
if os.name == "nt":
    os.environ["APPDATA"] = os.path.join(_TEST_HOME, "AppData", "Roaming")
    os.environ["LOCALAPPDATA"] = os.path.join(_TEST_HOME, "AppData", "Local")
# Linux: Laufzeitordner (Sockets, PID-Dateien) liegt sonst unter /run/user/<uid>.
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TEST_HOME, "run")
os.makedirs(os.environ["XDG_RUNTIME_DIR"], mode=0o700)

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
