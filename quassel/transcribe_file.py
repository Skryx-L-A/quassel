"""Datei-Transkription ohne UI- oder Zwischenablagen-Effekte."""
import os
import shutil
import subprocess
import tempfile
import wave
from array import array

from .audio import RATE, _Polyphase

SUPPORTED_EXTS = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4", ".mkv", ".webm", ".mov", ".avi")
MAX_CHUNK_SECONDS = 12


def is_supported(path):
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


def ffmpeg_cmd(src, dst_wav):
    return ["ffmpeg", "-nostdin", "-y", "-i", src, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "wav", dst_wav]


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None


def _wav_without_ffmpeg(src, dst):
    """16-bit WAV direkt oder mit dem bestehenden Polyphase-Resampler."""
    try:
        with wave.open(src, "rb") as inp, wave.open(dst, "wb") as out:
            channels, width, source_rate = inp.getnchannels(), inp.getsampwidth(), inp.getframerate()
            if width != 2 or channels < 1:
                return False
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE)
            resample = _Polyphase(source_rate, RATE) if source_rate != RATE else None
            while data := inp.readframes(8192):
                if channels != 1: data = _mono16(data, channels)
                out.writeframes(resample.feed(data) if resample else data)
        return True
    except (OSError, wave.Error, EOFError, ImportError):
        return False


def _mono16(data, channels):
    """Interleaved signed-16 PCM ohne das in Python 3.13 entfernte audioop."""
    samples = array("h"); samples.frombytes(data)
    if samples.itemsize != 2:
        return data
    mixed = array("h", (int(sum(samples[i:i + channels]) / channels)
                        for i in range(0, len(samples), channels)))
    return mixed.tobytes()


def to_wav16k(src, dst_wav, timeout=600):
    if str(src).lower().endswith(".wav") and _wav_without_ffmpeg(src, dst_wav): return True
    if not have_ffmpeg(): return False
    try: result = subprocess.run(ffmpeg_cmd(src, dst_wav), capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired): return False
    return result.returncode == 0 and os.path.exists(dst_wav)


def _cancelled(event): return bool(event and event.is_set())


def _chunks(path):
    with wave.open(path, "rb") as source:
        block = max(1, source.getframerate() * MAX_CHUNK_SECONDS)
        while data := source.readframes(block):
            fd, chunk = tempfile.mkstemp(suffix=".wav", prefix="quassel-file-chunk-"); os.close(fd)
            with wave.open(chunk, "wb") as out:
                out.setparams(source.getparams()); out.writeframes(data)
            yield chunk, len(data) // (source.getnchannels() * source.getsampwidth())


def transcribe_file(path, cfg, timeout=600, *, progress=None, cancel_event=None):
    """Datei zu Text; Callback nach jedem Chunk, Event bricht sicher ab.

    Die Rückgabe ist Text; Kopieren ist ausschließlich Aufgabe des Aufrufers.
    """
    if not is_supported(path): return None
    from . import whisperclient
    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="quassel-file-"); os.close(fd)
    created = []
    try:
        if _cancelled(cancel_event) or not to_wav16k(path, wav, timeout): return None
        if _cancelled(cancel_event) or not whisperclient.ensure_server(deadline=timeout): return None
        with wave.open(wav, "rb") as source: total = source.getnframes() / source.getframerate()
        done, text = 0.0, []
        for chunk, frames in _chunks(wav):
            created.append(chunk)
            if _cancelled(cancel_event): return None
            value = whisperclient.transcribe(chunk, cfg, timeout=timeout)
            if value is None: return None
            text.append(value); done += frames / RATE
            if progress: progress(min(done, total), total)
        return " ".join(part.strip() for part in text if part).strip()
    finally:
        for item in created + [wav]:
            try: os.remove(item)
            except OSError: pass
