"""Partial-loop wiring, using PCM and server fakes only."""
from types import SimpleNamespace

from quassel import dictation
from quassel.audio import RATE, SAMPLE_BYTES
from quassel.streaming import StreamTyper


class StopAfter:
    def __init__(self, count):
        self.count = count

    def wait(self, delay):
        self.count -= 1
        return self.count < 0

    def is_set(self):
        return False


def test_partial_loop_bounds_pcm_and_passes_confirmed_prompt(monkeypatch):
    seconds = iter((2, 15, 18, 20))
    class Recorder:
        active = True
        started = 0

        def raw_bytes(self):
            return b"\0" * (next(seconds) * RATE * SAMPLE_BYTES)

    typed = []
    owner = SimpleNamespace(_handsfree=True, states=[])
    owner.streamer = StreamTyper("word", typed.append, lambda n: None)
    owner._wav_from_raw = lambda data, path: wav_sizes.append(len(data))
    owner._state = lambda state, text="": owner.states.append((state, text))
    cfg = SimpleNamespace(pill_preview=True, language="de")
    loop = dictation.PartialLoop(Recorder(), cfg, owner)
    loop.stop_event = StopAfter(4)
    wav_sizes, prompts = [], []
    hypotheses = iter(("Wir besprechen den Plan", "Wir besprechen den Plan",
                       "den Plan und gehen weiter", "den Plan und gehen weiter"))
    monkeypatch.setattr(dictation.whisperclient, "ensure_server", lambda: True)
    def transcribe(path, cfg, timeout, prompt_extra=None):
        prompts.append(prompt_extra)
        return SimpleNamespace(text=next(hypotheses), language="de")
    monkeypatch.setattr(dictation.whisperclient, "transcribe_result", transcribe)
    monkeypatch.setattr(dictation, "postprocess_transcript",
                        lambda raw, cfg, language: ("text", raw))

    loop._loop("/tmp/unused-streaming-loop.wav")

    assert max(wav_sizes) <= 15 * RATE * SAMPLE_BYTES
    assert "".join(typed).startswith("Wir besprechen den Plan")
    assert prompts[2] == "Wir besprechen den Plan"
    assert owner.states[2] == ("recording", "und gehen weiter")
    assert owner.states[3] == ("recording", "")


def test_partial_loop_discards_result_after_emergency_stop(monkeypatch):
    class Recorder:
        active = True
        started = 0

        def raw_bytes(self):
            return b"\0" * (RATE * SAMPLE_BYTES)

    class Stop(StopAfter):
        stopped = False

        def is_set(self):
            return self.stopped

    typed = []
    owner = SimpleNamespace(_handsfree=True)
    owner.streamer = StreamTyper("word", typed.append, lambda n: None)
    owner._wav_from_raw = lambda data, path: None
    owner._state = lambda state, text="": None
    loop = dictation.PartialLoop(Recorder(), SimpleNamespace(pill_preview=False), owner)
    loop.stop_event = Stop(1)
    monkeypatch.setattr(dictation.whisperclient, "ensure_server", lambda: True)

    def stop_during_server_call(path, cfg, timeout):
        loop.stop_event.stopped = True
        return SimpleNamespace(text="Nicht tippen", language="de")

    monkeypatch.setattr(dictation.whisperclient, "transcribe_result",
                        stop_during_server_call)
    loop._loop("/tmp/unused-emergency-stream.wav")
    assert typed == []


def test_partial_loop_server_timeout_preserves_confirmed_text(monkeypatch):
    class Recorder:
        active = True
        started = 0

        def raw_bytes(self):
            return b"\0" * (RATE * SAMPLE_BYTES)

    typed = []
    owner = SimpleNamespace(_handsfree=True)
    owner.streamer = StreamTyper("word", typed.append, lambda n: None)
    owner.streamer.update("Schon da")
    owner.streamer.update("Schon da")
    owner._wav_from_raw = lambda data, path: None
    owner._state = lambda state, text="": None
    loop = dictation.PartialLoop(Recorder(), SimpleNamespace(pill_preview=False), owner)
    loop.stop_event = StopAfter(1)
    monkeypatch.setattr(dictation.whisperclient, "ensure_server", lambda: True)

    def timeout(path, cfg, timeout, prompt_extra):
        raise TimeoutError

    monkeypatch.setattr(dictation.whisperclient, "transcribe_result", timeout)
    loop._loop("/tmp/unused-timeout-stream.wav")
    assert "".join(typed) == "Schon da"
