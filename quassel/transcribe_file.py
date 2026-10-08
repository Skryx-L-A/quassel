"""Datei-Transkription ohne UI- oder Zwischenablagen-Effekte."""
from dataclasses import dataclass
import json
import os
import shutil
import subprocess
import tempfile
import wave
from array import array

from .audio import RATE, _Polyphase

SUPPORTED_EXTS = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4", ".mkv", ".webm", ".mov", ".avi")
MAX_CHUNK_SECONDS = 12


@dataclass(frozen=True)
class FileTranscript:
    """Text plus optionale, auf die Originaldatei bezogene Segmente."""
    text: str
    segments: tuple


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


def _seconds(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _offset_segments(raw, offset):
    """Valid segments receive the exact preceding-chunk duration as offset."""
    out = []
    for segment in raw or ():
        if not isinstance(segment, dict):
            continue
        start, end = _seconds(segment.get("start")), _seconds(segment.get("end"))
        text = segment.get("text")
        if start is None or end is None or end < start or not isinstance(text, str):
            continue
        out.append({"start": offset + start, "end": offset + end, "text": text.strip()})
    return out


def _transcribe_verbose(wavpath, cfg, timeout):
    """Request whisper.cpp's verbose JSON without changing normal dictation."""
    from . import config, whisperclient
    fields = [(name, "verbose_json" if name == "response_format" else value)
              for name, value in whisperclient.inference_fields(
                  wavpath, cfg, config.dictionary_words(), timeout)]
    try:
        body, content_type = whisperclient._multipart_body(wavpath, fields)
        payload = whisperclient._post_multipart(body, content_type, timeout)
        decoded = json.loads(payload) if payload is not None else None
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(decoded, dict) or not isinstance(decoded.get("text"), str):
        return None
    return decoded


def _chunks(path):
    with wave.open(path, "rb") as source:
        block = max(1, source.getframerate() * MAX_CHUNK_SECONDS)
        while data := source.readframes(block):
            fd, chunk = tempfile.mkstemp(suffix=".wav", prefix="quassel-file-chunk-"); os.close(fd)
            with wave.open(chunk, "wb") as out:
                out.setparams(source.getparams()); out.writeframes(data)
            yield chunk, len(data) // (source.getnchannels() * source.getsampwidth())


def _timestamp(seconds, decimal=","):
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{decimal}{milliseconds:03}"


def render_srt(segments):
    return "\n\n".join(f"{index}\n{_timestamp(item['start'])} --> {_timestamp(item['end'])}\n{item['text']}"
                       for index, item in enumerate(segments, 1) if item["text"])


def render_vtt(segments):
    body = "\n\n".join(f"{_timestamp(item['start'], '.')} --> {_timestamp(item['end'], '.')}\n{item['text']}"
                         for item in segments if item["text"])
    return "WEBVTT\n\n" + body + ("\n" if body else "")


def transcribe_file(path, cfg, timeout=600, *, progress=None, cancel_event=None,
                    output_format="text"):
    """File transcription with optional timestamps, progress, and cancellation.

    ``text`` preserves the former string return value. ``verbose_json`` returns
    :class:`FileTranscript`; ``srt`` and ``vtt`` render its normalized segments.
    """
    if not is_supported(path): return None
    if output_format not in ("text", "verbose_json", "srt", "vtt"):
        raise ValueError("unsupported output format")
    from . import whisperclient
    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="quassel-file-"); os.close(fd)
    created = []
    try:
        if _cancelled(cancel_event) or not to_wav16k(path, wav, timeout): return None
        if _cancelled(cancel_event) or not whisperclient.ensure_server(deadline=timeout): return None
        with wave.open(wav, "rb") as source: total = source.getnframes() / source.getframerate()
        done, text, segments = 0.0, [], []
        for chunk, frames in _chunks(wav):
            created.append(chunk)
            if _cancelled(cancel_event): return None
            if output_format == "text":
                value = whisperclient.transcribe(chunk, cfg, timeout=timeout)
                if value is None: return None
                text.append(value)
            else:
                value = _transcribe_verbose(chunk, cfg, timeout)
                if value is None: return None
                text.append(value["text"])
                segments.extend(_offset_segments(value.get("segments"), done))
            done += frames / RATE
            if progress: progress(min(done, total), total)
        transcript = FileTranscript(" ".join(part.strip() for part in text if part).strip(),
                                    tuple(segments))
        if output_format == "text": return transcript.text
        if output_format == "verbose_json": return transcript
        return render_srt(transcript.segments) if output_format == "srt" else render_vtt(transcript.segments)
    finally:
        for item in created + [wav]:
            try: os.remove(item)
            except OSError: pass
