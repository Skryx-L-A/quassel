"""Tests dürfen die echte Installation nicht berühren.

test_center_mac schrieb einmal über das Wörterbuch-Feld des Kontrollzentrums in
das echte dictionary.txt, weil sein Fixture CONFDIR umbog, aber nicht
config.DICTIONARY (zur Importzeit berechnet). Jeder Testlauf ersetzte so das
Wörterbuch der installierten App. Darum zeigt in Tests das ganze Home auf einen
Wegwerfordner, bevor quassel importiert wird.
"""
import os
import pwd

from quassel import config, state

REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def _inside_real_home(path):
    return os.path.realpath(path).startswith(REAL_HOME + os.sep)


def test_config_paths_are_not_in_the_real_home():
    for name in ("CONFDIR", "DATADIR", "CONFIG", "DICTIONARY", "LEARNED",
                 "REPLACEMENTS", "HISTORY", "AI_MODES"):
        assert not _inside_real_home(getattr(config, name)), name


def test_run_dir_is_not_in_the_real_home():
    assert not _inside_real_home(state.RUNDIR)
