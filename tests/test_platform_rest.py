"""Offline regression checks for platform event wiring."""
import ctypes
import threading

from quassel import daemon, power


def test_linux_reset_forgets_pressed_keys():
    d = daemon.Daemon.__new__(daemon.Daemon)
    d._linux_reset = threading.Event()
    pressed = daemon.PressedKeys()
    pressed.press(12, 29)
    assert not d._consume_linux_reset(pressed)
    d._linux_reset.set()
    assert d._consume_linux_reset(pressed)
    assert not pressed


def test_windows_native_filter_routes_power_message(monkeypatch):
    import test_win_stubs
    app = test_win_stubs.load_win_app(monkeypatch)
    from ctypes import wintypes
    events = []
    filt = app._PowerMessageFilter(power.PowerEvents(
        lambda: events.append("sleep"), lambda: events.append("wake")))
    msg = wintypes.MSG()
    msg.message = power.WM_POWERBROADCAST
    msg.wParam = power.PBT_APMSUSPEND
    assert filt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)
    msg.wParam = power.PBT_APMRESUMEAUTOMATIC
    filt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
    assert events == ["sleep", "wake"]
