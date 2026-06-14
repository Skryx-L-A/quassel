"""Tests der reinen Versions-Vergleichslogik des Update-Checks (kein Netzwerk)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.updatecheck import parse_version, compare_versions, is_newer


def test_parse_version():
    assert parse_version("v2.3.0") == (2, 3, 0)
    assert parse_version("2.2.0") == (2, 2, 0)
    assert parse_version("2.2.0-beta") == (2, 2, 0)
    assert parse_version("v2") == (2, 0, 0)
    assert parse_version("garbage") == (0, 0, 0)


def test_compare_versions():
    assert compare_versions("2.2.0", "2.3.0") == -1   # a < b
    assert compare_versions("2.3.0", "2.2.0") == 1    # a > b
    assert compare_versions("2.2.0", "2.2.0") == 0    # a == b


def test_is_newer():
    assert is_newer("2.2.0", "2.3.0") is True
    assert is_newer("2.3.0", "2.2.0") is False
    assert is_newer("2.2.0", "2.2.0") is False        # gleich -> nicht neuer
    assert is_newer("2.2.0", "2.2.1") is True
    # numerisch, nicht lexikografisch: 2.10.0 ist neuer als 2.9.0
    assert is_newer("2.10.0", "2.9.0") is False


if __name__ == "__main__":
    test_parse_version()
    test_compare_versions()
    test_is_newer()
    print("ok")
