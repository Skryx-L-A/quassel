"""Tests des Plattform-Dispatch (quassel/platform.py): darwin -> platform_mac,
sonst -> platform_linux. Backend-Import passiert erst bei Attributzugriff
(PEP 562), daher hier per sys.modules-Stub statt echtem platform_mac.py."""
import sys
import types
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _reload_platform():
    sys.modules.pop("quassel.platform", None)
    import quassel.platform as p
    return p


def test_dispatches_to_linux_backend(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    p = _reload_platform()
    import quassel.platform_linux as backend
    assert p.notify is backend.notify
    assert p.paste is backend.paste


def test_dispatches_to_mac_backend(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    stub = types.ModuleType("quassel.platform_mac")

    def fake_notify(text, ms=4000):
        pass
    stub.notify = fake_notify
    stub.paste = lambda text: None
    stub.mic_is_bluetooth = lambda mic="default": False
    stub.send_backspaces = lambda n: None
    stub.send_enter = lambda: None
    stub.type_chunk = lambda text: None
    stub.streaming_begin = lambda: None
    stub.streaming_restore = lambda old: None
    monkeypatch.setitem(sys.modules, "quassel.platform_mac", stub)

    p = _reload_platform()
    assert p.notify is fake_notify


def test_unknown_attribute_raises():
    p = _reload_platform()
    try:
        p.does_not_exist
    except AttributeError:
        pass
    else:
        raise AssertionError("expected AttributeError")
