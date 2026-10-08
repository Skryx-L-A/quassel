"""Power event routing with no OS notifications or subprocesses."""
from quassel import power


def test_windows_power_messages_route_sleep_and_wake():
    events = []
    hook = power.PowerEvents(lambda: events.append("sleep"),
                             lambda: events.append("wake"))
    assert hook.windows_message(power.WM_POWERBROADCAST, power.PBT_APMSUSPEND)
    assert hook.windows_message(power.WM_POWERBROADCAST, power.PBT_APMRESUMEAUTOMATIC)
    assert not hook.windows_message(0, power.PBT_APMSUSPEND)
    assert events == ["sleep", "wake"]


def test_stop_without_started_hook_is_safe():
    power.PowerEvents(lambda: None, lambda: None).stop()
