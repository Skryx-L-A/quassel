"""Tests für PartialLoop (daemon.py): adaptiver Abstand + Vorab-Abbruch der
Live-Vorschau-Transkription, ohne echten whisper-server.

whisperclient.transcribe wird gemockt, ebenso time.monotonic für den
adaptiven Abstand -- kein Thread wird gestartet, run() läuft synchron im
Testthread (stop_event ist ein FakeStopEvent, kein echtes threading.Event)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import daemon as daemon_mod
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

    def raw_bytes(self):
        return b"\x00" * (RATE * SAMPLE_BYTES * 2)   # 2s Audio, > 0,5s Minimum


def make_loop(monkeypatch, stop_after=1):
    monkeypatch.setattr(daemon_mod.whisperclient, "ensure_server", lambda: None)
    monkeypatch.setattr(daemon_mod, "wav_from_raw", lambda data, path: None)
    monkeypatch.setattr(daemon_mod, "state_set", lambda *a, **k: None)
    monkeypatch.setattr(daemon_mod.textproc, "postprocess",
                         lambda raw, cfg: ("text", raw))
    rec = FakeRecorder()
    cfg = SimpleNamespace(pill_preview=True)
    owner = SimpleNamespace(streamer=None)
    pl = daemon_mod.PartialLoop(rec, cfg, owner)
    pl.stop_event = FakeStopEvent(stop_after=stop_after)
    return pl, rec


def make_clock(monkeypatch, values):
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

    monkeypatch.setattr(daemon_mod, "wav_from_raw", fake_wav_from_raw)
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

    monkeypatch.setattr(daemon_mod, "wav_from_raw", fake_wav_from_raw)
    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe", fake_transcribe)

    pl.run()

    assert transcribe_calls == []


def test_normal_run_still_transcribes_and_updates_state(monkeypatch):
    """Regressionsschutz: der normale Weg (kein Abbruch) transkribiert weiter
    und setzt den Vorschautext."""
    pl, rec = make_loop(monkeypatch, stop_after=1)
    transcribe_calls = []
    seen_state = []

    monkeypatch.setattr(daemon_mod, "state_set",
                         lambda *a, **k: seen_state.append(a))

    def fake_transcribe(path, cfg, timeout=20):
        transcribe_calls.append(path)
        return "hallo welt"

    monkeypatch.setattr(daemon_mod.whisperclient, "transcribe", fake_transcribe)
    make_clock(monkeypatch, [0.0, 1.0])

    pl.run()

    assert transcribe_calls == [daemon_mod.PARTWAV]
    assert seen_state == [("recording", "hallo welt")]
