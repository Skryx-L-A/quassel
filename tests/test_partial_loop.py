"""Tests für PartialLoop (dictation.py, von daemon.py und win/app.py geteilt): adaptiver Abstand + Vorab-Abbruch der
Live-Vorschau-Transkription, ohne echten whisper-server.

whisperclient.transcribe wird gemockt, ebenso time.monotonic für den
adaptiven Abstand -- kein Thread wird gestartet, run() läuft synchron im
Testthread (stop_event ist ein FakeStopEvent, kein echtes threading.Event)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import dictation as daemon_mod
from quassel.audio import RATE, SAMPLE_BYTES


class FakeStopEvent:
    """Ersatz für threading.Event: zeichnet jeden wait(timeout)-Aufruf auf und
    liefert nach `stop_after` False-Antworten True (Schleifenende) -- wie ein
    echtes Event, das irgendwann von finish()/panic_stop() gesetzt wird."""

    def __init__(self, stop_after=1):
        self.calls = []
        self.stop_after = stop_after
        self._is_set = False

    def wait(self, timeout):
        self.calls.append(timeout)
        if len(self.calls) > self.stop_after:
            self._is_set = True
            return True
        return False

    def is_set(self):
        return self._is_set

    def set(self):
        self._is_set = True


class FakeRecorder:
    def __init__(self):
        self.active = True
        self.started = 0.0

    def raw_bytes(self):
        return b"\x00" * (RATE * SAMPLE_BYTES * 2)   # 2s Audio, > 0,5s Minimum


class FakeOwner:
    """Ablaufsteuerung-Attrappe: nur die Plattform-Anbindung, die die
    Vorschau benutzt (WAV schreiben, Zustand melden)."""

    def __init__(self, handsfree=True):
        self.streamer = None
        self.states = []
        self.wav_hook = None
        # Freihand: die Halten-Sperre (C44) greift nicht, alte Tests bleiben gültig.
        self._handsfree = handsfree

    def _wav_from_raw(self, data, path):
        if self.wav_hook is not None:
            self.wav_hook(data, path)

    def _state(self, state, text=""):
        self.states.append((state, text))


def make_loop(monkeypatch, stop_after=1):
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server", lambda: None)
    monkeypatch.setattr(daemon_mod.textproc, "postprocess",
                         lambda raw, cfg: ("text", raw))
    rec = FakeRecorder()
    cfg = SimpleNamespace(pill_preview=True)
    owner = FakeOwner()
    pl = daemon_mod.PartialLoop(rec, cfg, owner)
    pl.stop_event = FakeStopEvent(stop_after=stop_after)
    return pl, rec


def make_clock(monkeypatch, values):
    """values: Schleifenstart und Ende eines Durchlaufs; dazwischen liest die
    Schleife die Uhr noch einmal für die Laufzeit-Markierung (C44)."""
    values = [values[0], values[0]] + list(values[1:])
    it = iter(values)
    monkeypatch.setattr(daemon_mod.time, "monotonic", lambda: next(it))


def test_adaptive_delay_follows_last_run_duration(monkeypatch):
    """Ein Durchlauf, der 5s dauert, hebt den nächsten Abstand auf 5s an
    (statt der festen PARTIAL_EVERY = 2s) -- das hält den Server strukturell
    unter 50% Auslastung."""
    pl, rec = make_loop(monkeypatch, stop_after=1)
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                         lambda path, cfg, timeout=20: "hallo")
    make_clock(monkeypatch, [0.0, 5.0])   # iter_start=0.0, finally=5.0 -> 5s

    pl.run()

    assert pl.stop_event.calls[0] == daemon_mod.PARTIAL_EVERY   # erster Abstand: fix
    assert pl.stop_event.calls[1] == 5.0                        # zweiter: adaptiv


def test_adaptive_delay_is_capped_at_partial_max_wait(monkeypatch):
    """Ein Ausreißer (50s) darf die Vorschau nicht minutenlang verstummen
    lassen -- Obergrenze PARTIAL_MAX_WAIT greift."""
    pl, rec = make_loop(monkeypatch, stop_after=1)
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                         lambda path, cfg, timeout=20: "hallo")
    make_clock(monkeypatch, [0.0, 50.0])

    pl.run()

    assert pl.stop_event.calls[1] == daemon_mod.PARTIAL_MAX_WAIT


def test_adaptive_delay_never_drops_below_partial_every(monkeypatch):
    """Ein sehr schneller Durchlauf (0,1s) darf den Abstand nicht unter die
    Untergrenze PARTIAL_EVERY drücken."""
    pl, rec = make_loop(monkeypatch, stop_after=1)
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                         lambda path, cfg, timeout=20: "hallo")
    make_clock(monkeypatch, [0.0, 0.1])

    pl.run()

    assert pl.stop_event.calls[1] == daemon_mod.PARTIAL_EVERY


def test_transcribe_skipped_when_dictation_ends_just_before_it(monkeypatch):
    """Endet das Diktat (rec.active -> False) zwischen dem Bau der Vorschau-WAV
    und dem transcribe()-Aufruf, darf transcribe() gar nicht erst starten --
    sonst reiht sich ein unnötiges Teiltranskript vor dem Finale in die
    Warteschlange des Servers ein."""
    pl, rec = make_loop(monkeypatch, stop_after=1)
    transcribe_calls = []

    def fake_wav_from_raw(data, path):
        rec.active = False   # Diktat endet genau hier -> Finale steht an

    def fake_transcribe(path, cfg, timeout=20):
        transcribe_calls.append(path)
        return "hallo"

    pl.owner.wav_hook = fake_wav_from_raw
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe", fake_transcribe)

    pl.run()

    assert transcribe_calls == []


def test_transcribe_skipped_when_stop_event_set_just_before_it(monkeypatch):
    """Dieselbe Prüfung für den Not-Aus/finish()-Pfad: wird stop_event
    zwischen WAV-Bau und transcribe() gesetzt, startet transcribe() nicht."""
    pl, rec = make_loop(monkeypatch, stop_after=1)
    transcribe_calls = []

    def fake_wav_from_raw(data, path):
        pl.stop_event.set()

    def fake_transcribe(path, cfg, timeout=20):
        transcribe_calls.append(path)
        return "hallo"

    pl.owner.wav_hook = fake_wav_from_raw
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe", fake_transcribe)

    pl.run()

    assert transcribe_calls == []


def test_normal_run_still_transcribes_and_updates_state(monkeypatch):
    """Regressionsschutz: der normale Weg (kein Abbruch) transkribiert weiter
    und setzt den Vorschautext."""
    pl, rec = make_loop(monkeypatch, stop_after=1)
    transcribe_calls = []

    def fake_transcribe(path, cfg, timeout=20):
        transcribe_calls.append(path)
        return "hallo welt"

    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe", fake_transcribe)
    make_clock(monkeypatch, [0.0, 1.0])

    pl.run()

    assert transcribe_calls == [daemon_mod.scratch_wav("partial")]
    assert pl.owner.states == [("recording", "hallo welt")]


# ------------------------------------------------- C44: Halten-Modus
def _hold_loop(monkeypatch, now):
    pl, rec = make_loop(monkeypatch, stop_after=1)
    pl.owner._handsfree = False
    rec.started = 100.0
    calls = []
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                         lambda path, cfg, timeout=20: calls.append(path) or "hallo")
    monkeypatch.setattr(daemon_mod.time, "monotonic", lambda: now)
    return pl, calls


def test_short_hold_dictation_gets_no_preview_request(monkeypatch):
    """Kurze Halten-Diktate enden oft während der ersten Vorschau; deren
    Anfrage hielte das Finale am Server auf."""
    pl, calls = _hold_loop(monkeypatch, 100.0 + daemon_mod.PREVIEW_HOLD_MIN - 0.5)
    pl.run()
    assert calls == []


def test_long_hold_dictation_gets_its_preview(monkeypatch):
    pl, calls = _hold_loop(monkeypatch, 100.0 + daemon_mod.PREVIEW_HOLD_MIN + 0.5)
    pl.run()
    assert len(calls) == 1


def test_inflight_marker_is_set_only_during_the_request(monkeypatch):
    pl, rec = make_loop(monkeypatch, stop_after=1)
    seen = []
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe",
                         lambda path, cfg, timeout=20: seen.append(pl.inflight_since) or "x")
    pl.run()
    assert seen and seen[0] is not None
    assert pl.inflight_since is None


def test_finish_logs_a_running_preview(monkeypatch):
    from types import SimpleNamespace as NS
    logs = []
    core = daemon_mod.DictationCore.__new__(daemon_mod.DictationCore)
    core._log = logs.append
    core.partial = NS(inflight_since=daemon_mod.time.monotonic() - 1.0,
                      stop=lambda: None)
    core.rec = NS(stop=lambda: None, raw_bytes=lambda path=None: b"")
    core.cfg = NS(beep=False)
    core.ducker = NS(restore=lambda: None)
    core._state = lambda *a: None
    core._notify = lambda t: None
    monkeypatch.setattr(daemon_mod, "TAIL_PAD_MS", 0)
    assert core._finish_capture() is None
    assert any("Vorschau lief beim Loslassen" in m for m in logs), logs
