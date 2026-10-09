"""Doppelte Helfer (K134): Kontrollzentrum und Pille holen ihr Icon aus
derselben Quelle, statt zwei Kopien von Pfadliste und Suche zu pflegen."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from quassel import center, pill_qt                       # noqa: E402


def test_center_uses_the_pill_icon_helper():
    assert center.app_icon is pill_qt.app_icon
    assert not hasattr(center, "ICON_PATHS")
