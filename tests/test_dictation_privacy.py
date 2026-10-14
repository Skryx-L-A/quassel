"""Audit A6: Rohaudio, WAV und der letzte Text bleiben nicht auf der Platte.

Unter macOS und Windows liegt das Laufzeitverzeichnis nicht im RAM (wie
tmpfs unter Linux), sondern unter Application Support bzw. LOCALAPPDATA —
und damit in Backups. Alle Dateien hier liegen in tmp_path."""
import json
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import dictation, state


class FileRec:
    """Recorder-Attrappe, die wie der echte eine Rohdatei hinterlässt."""

    def __init__(self, tmp_path, data=b"\x01\x00" * 20000):
        self.path = str(tmp_path / "rec.raw")
        self.data = data
        self.active = False
        self.started = 0.0
        self.raw_path = self.path

    def start(self, mic="default"):
        with open(self.path, "wb") as f:
            f.write(self.data)
        self.active = True
        return True

    def stop(self):
        self.active = False
        return self.path

    def raw_bytes(self, path=None):
        with open(path or self.path, "rb") as f:
            return f.read()


class NoPartial:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class Core(dictation.DictationCore):
    def __init__(self, tmp_path, rec):
        self.tmp = tmp_path
        self.rec = rec
        self.cfg = SimpleNamespace(
            beep=False, beep_output="system", mic="default", mute_mode="off",
            ui_language="auto", streaming=False, ai_enabled=False,
            programmer_mode=False, text_replace=False, history_enabled=False,
            stats_enabled=False, auto_learn=False, reload=lambda: False)
        self.ducker = SimpleNamespace(apply=lambda m: None, restore=lambda: None)
        self.states, self.pasted, self.wavs, self.cleared = [], [], [], []
        self.token = None

    def _state(self, s, text=""):
        self.states.append((s, text))
        return self.token

    def _clear_state_text(self, token):
        self.cleared.append(token)

    def _notify(self, text):
        pass

    def _log(self, msg):
        pass

    def _paste(self, text):
        self.pasted.append(text)

    def _wav_from_raw(self, data, path):
        self.wavs.append(path)
        with open(path, "wb") as f:
            f.write(data)


def setup(monkeypatch, tmp_path, server_up=True, transcript="hallo welt"):
    monkeypatch.setattr(dictation, "WAV", str(tmp_path / "rec.wav"))
    monkeypatch.setattr(dictation, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(dictation, "TAIL_PAD_MS", 0)
    monkeypatch.setattr(dictation, "PartialLoop", NoPartial)
    monkeypatch.setattr(dictation.beep, "set_output", lambda n: None)
    monkeypatch.setattr(dictation.whisperclient, "server_was_up", lambda: True)
    monkeypatch.setattr(dictation.whisperclient, "ensure_server",
                        lambda deadline=600: server_up)
    monkeypatch.setattr(dictation.whisperclient, "transcribe", lambda *a, **k: transcript)
    monkeypatch.setattr(dictation.textproc, "postprocess",
                        lambda raw, cfg: ("text", transcript))
    core = Core(tmp_path, FileRec(tmp_path))
    return core


def test_audio_files_are_gone_after_the_text_is_inserted(monkeypatch, tmp_path):
    core = setup(monkeypatch, tmp_path)
    assert core.start_recording()
    core.finish_now()
    assert core.pasted == ["hallo welt"]
    assert core.wavs and not any(os.path.exists(p) for p in core.wavs)
    assert not os.path.exists(core.rec.path)
    assert sorted(os.listdir(tmp_path)) == []


def test_a_rescued_recording_keeps_only_the_rescue(monkeypatch, tmp_path):
    core = setup(monkeypatch, tmp_path, server_up=False)
    assert core.start_recording()
    core.finish_now()
    left = sorted(os.listdir(tmp_path))
    assert len(left) == 1 and left[0].startswith("rescued-")


def test_raw_file_stays_when_even_the_rescue_fails(monkeypatch, tmp_path):
    core = setup(monkeypatch, tmp_path, server_up=False)

    def unwritable(data, path):
        raise OSError("Platte voll")
    core._wav_from_raw = unwritable
    assert core.start_recording()
    core.finish_now()
    assert os.path.exists(core.rec.path)          # letzte Kopie des Diktats


def test_canceled_and_too_short_recordings_leave_no_audio(monkeypatch, tmp_path):
    core = setup(monkeypatch, tmp_path)
    assert core.start_recording()
    core.cancel_recording("canceled_key")
    assert not os.path.exists(core.rec.path)
    core.rec.data = b"\x01\x00" * 100
    assert core.start_recording()
    assert core.finish() is None
    assert not os.path.exists(core.rec.path)


def test_the_text_is_cleared_after_display(monkeypatch, tmp_path):
    monkeypatch.setattr(dictation, "STATE_TEXT_KEEP", 0.01)
    core = setup(monkeypatch, tmp_path)
    core.token = 1234.5
    cleared = threading.Event()
    core._clear_state_text = lambda token: core.cleared.append(token) or cleared.set()
    assert core.start_recording()
    core.finish_now()
    assert cleared.wait(2.0)
    assert core.cleared == [1234.5]


def test_preview_wav_is_removed_when_the_loop_ends(monkeypatch, tmp_path):
    wav = tmp_path / "partial-1.wav"
    wav.write_bytes(b"alt")
    loop = dictation.PartialLoop(SimpleNamespace(active=False), SimpleNamespace(),
                                 SimpleNamespace(streamer=None), wav_path=str(wav))
    monkeypatch.setattr(dictation.whisperclient, "ensure_server", lambda: None)
    loop.stop_event.set()
    loop.run()
    assert not wav.exists()


def test_scratch_files_carry_the_process_id(monkeypatch, tmp_path):
    """Aufräumen trifft nie die Datei eines anderen Quassel-Prozesses."""
    monkeypatch.setattr(dictation, "WAV", str(tmp_path / "rec.wav"))
    assert dictation.scratch_wav("rec") == str(tmp_path / ("rec-%d.wav" % os.getpid()))


def test_discard_ignores_relative_and_missing_paths(tmp_path):
    dictation.discard(None)
    dictation.discard("rec.raw")
    dictation.discard(str(tmp_path / "fehlt.raw"))


# ------------------------------------------------------------- state.json
def _state_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "RUNDIR", str(tmp_path))
    monkeypatch.setattr(state, "STATE", str(tmp_path / "state.json"))


def test_state_clear_text_keeps_state_and_timestamp(monkeypatch, tmp_path):
    _state_paths(monkeypatch, tmp_path)
    ts = state.state_set("done", "geheimer Text")
    assert state.state_clear_text(ts) is True
    with open(tmp_path / "state.json", encoding="utf-8") as f:
        st = json.load(f)
    assert st == {"state": "done", "text": "", "ts": ts}


def test_state_set_gives_every_message_its_own_timestamp(monkeypatch, tmp_path):
    """Die Windows-Uhr tickt nur alle ~15 ms: zwei Meldungen im selben Takt
    müssen trotzdem unterscheidbar bleiben (state_clear_text, Pille)."""
    _state_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(state.time, "time", lambda: 1700000000.0)   # eingefrorene Uhr
    stamps = [state.state_set("recording", str(i)) for i in range(3)]
    assert stamps == sorted(set(stamps))
    assert state.state_read()["ts"] == stamps[-1]


def test_state_clear_text_leaves_a_newer_message_alone(monkeypatch, tmp_path):
    _state_paths(monkeypatch, tmp_path)
    old = state.state_set("done", "alt")
    state.state_set("recording", "neue Vorschau")
    assert state.state_clear_text(old) is False
    assert state.state_read()["text"] == "neue Vorschau"
