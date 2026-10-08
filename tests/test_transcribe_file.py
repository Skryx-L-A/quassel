"""Reine Logik-Tests für transcribe_file (kein ffmpeg/whisper-Aufruf)."""
import sys, os, wave
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.audio import RATE
from quassel.transcribe_file import ffmpeg_cmd, is_supported, to_wav16k


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


if __name__ == "__main__":
    test_is_supported()
    test_ffmpeg_cmd()
    print("ok")
