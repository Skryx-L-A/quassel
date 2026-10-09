"""Windows ducking fallbacks must not toggle unknown playback state."""
from quassel.win import audioctl


def test_unavailable_status_backends_never_send_toggle_keys(monkeypatch):
    monkeypatch.setattr(audioctl, "_HAVE_PYCAW", False)
    monkeypatch.setattr(audioctl, "_HAVE_SMTC", False)
    for mode in ("all", "music"):
        token = audioctl.duck_apply(mode)
        assert token is None
        audioctl.duck_restore(mode, token)


def test_failed_status_query_never_sends_toggle_keys(monkeypatch):
    monkeypatch.setattr(audioctl, "_HAVE_PYCAW", True)
    monkeypatch.setattr(audioctl, "_HAVE_SMTC", True)
    monkeypatch.setattr(audioctl, "_endpoint_volume", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(audioctl, "_smtc_pause_playing", lambda: (_ for _ in ()).throw(OSError()))
    assert audioctl.duck_apply("all") is None
    assert audioctl.duck_apply("music") is None
