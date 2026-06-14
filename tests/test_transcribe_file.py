"""Reine Logik-Tests für transcribe_file (kein ffmpeg/whisper-Aufruf)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel.audio import RATE
from quassel.transcribe_file import ffmpeg_cmd, is_supported


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


if __name__ == "__main__":
    test_is_supported()
    test_ffmpeg_cmd()
    print("ok")
