"""Transkription bestehender Audio-/Video-Dateien (Issue #21).

Eine vorhandene Datei wird per ffmpeg nach 16 kHz Mono s16-PCM-WAV dekodiert
und dann wie eine normale Aufnahme über whisperclient.transcribe verarbeitet.
"""
import os
import shutil
import subprocess
import tempfile

from .audio import RATE

SUPPORTED_EXTS = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma",
                  ".mp4", ".mkv", ".webm", ".mov", ".avi")   # Audio + Video (Tonspur)


def is_supported(path):
    """True, wenn die Dateiendung (ohne Gross/Klein) unterstützt wird."""
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


def ffmpeg_cmd(src, dst_wav):
    """ffmpeg-argv: dekodiere src nach 16 kHz Mono s16-PCM-WAV (ohne Video)."""
    return ["ffmpeg", "-nostdin", "-y", "-i", src,
            "-vn", "-ac", "1", "-ar", str(RATE),
            "-f", "wav", dst_wav]


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None


def to_wav16k(src, dst_wav, timeout=600):
    """Konvertiere src nach dst_wav; True bei Erfolg, sonst False (nie Fehler)."""
    if not have_ffmpeg():
        return False
    try:
        r = subprocess.run(ffmpeg_cmd(src, dst_wav), capture_output=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and os.path.exists(dst_wav)


def transcribe_file(path, cfg, timeout=600):
    """Datei -> Text. None, wenn nicht unterstützt, ffmpeg fehlt oder Fehler."""
    if not is_supported(path):
        return None
    if not have_ffmpeg():
        return None
    # whisperclient erst hier importieren -> Import des Moduls bleibt billig.
    from . import whisperclient

    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="quassel-file-")
    os.close(fd)
    try:
        if not to_wav16k(path, wav, timeout=timeout):
            return None
        if not whisperclient.ensure_server():
            return None
        return whisperclient.transcribe(wav, cfg, timeout=timeout)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
