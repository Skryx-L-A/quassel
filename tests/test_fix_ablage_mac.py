"""Offline regression for an unreadable macOS pasteboard item."""
from quassel import platform_mac as mac


def test_unsafe_paste_does_not_stick_after_new_user_copy(monkeypatch):
    board = {"snapshot": mac.UNSAFE}
    typed, copied = [], []
    monkeypatch.setattr(mac, "clip_snapshot", lambda: board["snapshot"])
    monkeypatch.setattr(mac, "_type_unicode", lambda text: typed.append(text) or True)
    monkeypatch.setattr(mac, "clip_copy", lambda text, concealed=False: copied.append(text))
    monkeypatch.setattr(mac, "_send_cmd_v", lambda: None)
    monkeypatch.setattr(mac, "_schedule_restore_locked", lambda delay: None)
    monkeypatch.setattr(mac.time, "sleep", lambda _: None)
    mac._saved_original = None
    mac._last_written = None
    assert mac.paste("first") is True
    board["snapshot"] = "new user copy"
    mac.paste("second")
    assert typed == ["first"]
    assert copied == ["second"]


def test_unsafe_streaming_slot_clears_at_restore(monkeypatch):
    typed = []
    monkeypatch.setattr(mac, "clip_snapshot", lambda: mac.UNSAFE)
    monkeypatch.setattr(mac, "_type_unicode", lambda text: typed.append(text) or True)
    monkeypatch.setattr(mac, "_schedule_restore_locked", lambda delay: (_ for _ in ()).throw(
        AssertionError("unsafe clipboard scheduled for restore")))
    mac._saved_original = None
    mac._last_written = None
    token = mac.streaming_begin()
    assert token is mac.UNSAFE
    mac.type_chunk("chunk")
    assert mac._saved_original is mac.UNSAFE
    mac.streaming_restore(token)
    assert mac._saved_original is None
    assert typed == ["chunk"]
