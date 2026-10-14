"""Tests dürfen die echte Installation nicht berühren.

test_center_mac schrieb einmal über das Wörterbuch-Feld des Kontrollzentrums in
das echte dictionary.txt, weil sein Fixture CONFDIR umbog, aber nicht
config.DICTIONARY (zur Importzeit berechnet). Jeder Testlauf ersetzte so das
Wörterbuch der installierten App. Darum zeigt in Tests das ganze Home auf einen
Wegwerfordner, bevor quassel importiert wird.
"""
import os

from quassel import config, state

# conftest merkt sich das echte Home, bevor es umgebogen wird.
REAL_HOME = os.path.realpath(os.environ["QUASSEL_TEST_REAL_HOME"])


# Unter Windows liegt das Temp-Verzeichnis selbst im echten Home
# (C:\Users\<name>\AppData\Local\Temp); der Wegwerf-Home von conftest darum
# ausnehmen, sonst gälte jeder Pfad darin als "echt".
TEST_HOME = os.path.realpath(os.environ["HOME"])


def _inside(path, root):
    return os.path.realpath(path).startswith(root + os.sep)


def _inside_real_home(path):
    return _inside(path, REAL_HOME) and not _inside(path, TEST_HOME)


def test_config_paths_are_not_in_the_real_home():
    for name in ("CONFDIR", "DATADIR", "CONFIG", "DICTIONARY", "LEARNED",
                 "REPLACEMENTS", "HISTORY", "AI_MODES"):
        assert not _inside_real_home(getattr(config, name)), name


def test_the_real_home_is_known():
    assert os.path.isdir(REAL_HOME)
    assert os.path.realpath(os.path.expanduser("~")) != REAL_HOME
    assert os.path.realpath(os.path.expanduser("~")) == TEST_HOME


def test_run_dir_is_not_in_the_real_home():
    assert not _inside_real_home(state.RUNDIR)
