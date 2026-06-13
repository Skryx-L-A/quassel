"""AudioDucker: Dispatch- und Zustandslogik (Backend gemockt, kein echtes Audio)."""
import quassel.mediacontrol as mc


class FakeBackend:
    def __init__(self):
        self.calls = []

    def duck_apply(self, mode):
        self.calls.append(("apply", mode))
        return {"mode": mode}

    def duck_restore(self, mode, token):
        self.calls.append(("restore", mode, token))


def test_apply_and_restore_all(monkeypatch):
    be = FakeBackend()
    monkeypatch.setattr(mc, "_backend", be)
    d = mc.AudioDucker()
    d.apply("all")
    d.restore()
    assert be.calls == [("apply", "all"), ("restore", "all", {"mode": "all"})]


def test_apply_and_restore_music(monkeypatch):
    be = FakeBackend()
    monkeypatch.setattr(mc, "_backend", be)
    d = mc.AudioDucker()
    d.apply("music")
    d.restore()
    assert be.calls == [("apply", "music"), ("restore", "music", {"mode": "music"})]


def test_off_is_noop(monkeypatch):
    be = FakeBackend()
    monkeypatch.setattr(mc, "_backend", be)
    d = mc.AudioDucker()
    d.apply("off")
    d.restore()
    assert be.calls == []


def test_double_apply_restores_previous_first(monkeypatch):
    be = FakeBackend()
    monkeypatch.setattr(mc, "_backend", be)
    d = mc.AudioDucker()
    d.apply("music")
    d.apply("all")        # muss zuerst "music" zurücknehmen
    assert ("restore", "music", {"mode": "music"}) in be.calls
    assert ("apply", "all") in be.calls


def test_restore_without_apply_is_safe(monkeypatch):
    be = FakeBackend()
    monkeypatch.setattr(mc, "_backend", be)
    d = mc.AudioDucker()
    d.restore()           # darf nichts tun und nicht werfen
    assert be.calls == []


def test_backend_exceptions_never_propagate(monkeypatch):
    class Boom:
        def duck_apply(self, mode):
            raise RuntimeError("apply kaputt")

        def duck_restore(self, mode, token):
            raise RuntimeError("restore kaputt")

    monkeypatch.setattr(mc, "_backend", Boom())
    d = mc.AudioDucker()
    d.apply("all")        # darf nicht werfen
    d.restore()           # darf nicht werfen


def test_missing_backend_is_safe(monkeypatch):
    monkeypatch.setattr(mc, "_backend", None)
    d = mc.AudioDucker()
    d.apply("all")
    d.restore()
