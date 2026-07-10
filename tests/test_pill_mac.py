"""mac-Zweige der Pille mit ECHTEM Qt (offscreen): Space-übergreifendes
Schweben (_mac_float_everywhere) wird beim Zeigen gesetzt und darf die
Pille nie sterben lassen — auch ohne erreichbares NSWindow (offscreen)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication   # noqa: E402

from quassel import pill_qt                   # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pill(qapp):
    p = pill_qt.Pill()
    yield p
    p.close()


def test_float_everywhere_never_raises(pill):
    # offscreen hat kein NSWindow — der Guard muss das schlucken
    pill._mac_float_everywhere()


def test_show_calls_float_everywhere_on_mac(pill, monkeypatch):
    monkeypatch.setattr(pill_qt.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(pill, "_mac_float_everywhere",
                        lambda: calls.append(1))
    pill.hide()
    pill.show()
    assert calls == [1]


def test_show_skips_float_on_other_platforms(pill, monkeypatch):
    monkeypatch.setattr(pill_qt.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(pill, "_mac_float_everywhere",
                        lambda: calls.append(1))
    pill.hide()
    pill.show()
    assert calls == []
