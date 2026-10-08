"""Reine Logik-Tests für transcribe_file (kein ffmpeg/whisper-Aufruf)."""
import json
import sys, os, wave
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.audio import RATE
from quassel import transcribe_file
from quassel.transcribe_file import (ffmpeg_cmd, is_supported, render_srt,
                                     render_vtt, to_wav16k)


def test_is_supported():
    assert is_supported("a.MP3")
    assert is_supported("x.wav")
    assert is_supported("clip.mkv")
    assert not is_supported("note.txt")
    assert not is_supported("a")


def test_ffmpeg_cmd():
    cmd = ffmpeg_cmd("in.mp3", "/tmp/o.wav")
    assert cmd[0] == "ffmpeg"
    assert "-ar" in cmd
    assert cmd[cmd.index("-ar") + 1] == str(RATE) == "16000"
    assert "in.mp3" in cmd
    assert "/tmp/o.wav" in cmd
    # Mono angefordert
    assert "-ac" in cmd
    assert cmd[cmd.index("-ac") + 1] == "1"


def test_pcm_wav_does_not_need_ffmpeg(tmp_path, monkeypatch):
    source, target = tmp_path / "source.wav", tmp_path / "target.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(b"\0\0" * 32)
    monkeypatch.setattr("quassel.transcribe_file.have_ffmpeg", lambda: False)
    assert to_wav16k(str(source), str(target))
    with wave.open(str(target), "rb") as out:
        assert (out.getnchannels(), out.getframerate()) == (1, 16000)


def test_verbose_segments_are_offset_across_chunks_and_rendered(tmp_path, monkeypatch):
    """The second chunk begins after actual frames, not a guessed 12 seconds."""
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE)
        out.writeframes(b"\0\0" * (RATE * 13))
    replies = iter((
        {"text": "first", "segments": [{"start": 0, "end": 11.5, "text": " first "}]},
        {"text": "second", "segments": [{"start": 0, "end": 1, "text": "second"}]},
    ))
    monkeypatch.setattr("quassel.whisperclient.ensure_server", lambda **kw: True)
    monkeypatch.setattr("quassel.transcribe_file._transcribe_verbose", lambda *a: next(replies))
    progress = []
    result = transcribe_file.transcribe_file(str(source), object(), output_format="verbose_json",
                                              progress=lambda done, total: progress.append((done, total)))
    assert result.text == "first second"
    assert result.segments[1]["start"] == 12.0
    assert progress[-1] == (13.0, 13.0)
    assert "00:00:12,000 --> 00:00:13,000" in render_srt(result.segments)
    assert render_vtt(result.segments).startswith("WEBVTT\n\n")


def test_cancelled_file_does_not_contact_the_server(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE); out.writeframes(b"\0\0")
    class Cancel:
        def is_set(self): return True
    monkeypatch.setattr("quassel.whisperclient.ensure_server",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("must not start server")))
    assert transcribe_file.transcribe_file(str(source), object(), cancel_event=Cancel()) is None


if __name__ == "__main__":
    test_is_supported()
    test_ffmpeg_cmd()
    print("ok")
