"""Variante über die Python-Bindung ``transcribe_cpp`` (optional).

PyPI (geprüft 2026-10-09): ``transcribe-cpp`` 0.3.1 ist reines Python
(ctypes) und lädt die native Bibliothek aus ``transcribe-cpp-native`` 0.3.1;
dessen Wheel ``macosx_11_0_arm64`` bringt Metal mit (Upstream
``pyproject.toml``: "macOS arm64 -> Metal (embedded shaders) + CPU"), die
Linux-/Windows-Wheels CPU+Vulkan, CUDA über ``transcribe-cpp-native-cu12``.

Unterschied zum CLI-Adapter: das Modell bleibt nach ``warmup()`` im Prozess
geladen (kein Ladevorgang je Clip), Kontexttext geht im Prozess an die
Bibliothek statt in eine Befehlszeile, und Wortzeiten kommen mit.

API laut ``bindings/python/src/transcribe_cpp/__init__.py`` (v0.3.1):
``Model(path, backend=…)`` (:925), ``model.session(n_threads=…)`` (:1042),
``Session.run(pcm, language=…, timestamps=…, vocabulary=…, prompt=…)``
(:1127-1139), ``Session.cancel()`` (:1415), ``Result.words`` (:574).
Ausnahmen tragen ``status`` (errors.py), Werte aus include/transcribe.h:199-315.
"""
import array
import os
import sys
import threading
import wave

from . import tcpp_families as fams
from .base import SpeechCapabilities, SpeechResult, TranscriptionOptions
from .transcribe_cpp import (COMPUTE_BACKENDS, NO_CAPABILITIES,
                             _vocabulary_words, wav_seconds)

# transcribe_status-Werte (include/transcribe.h:199-315)
_STATUS_ERRORS = {
    3: fams.ERR_MODEL_LOAD, 4: fams.ERR_MODEL_LOAD, 5: fams.ERR_MODEL_LOAD,
    6: fams.ERR_MODEL_LOAD, 7: fams.ERR_OOM, 8: fams.ERR_BACKEND,
    9: fams.ERR_AUDIO, 10: fams.ERR_LANGUAGE, 13: fams.ERR_TIMEOUT,
    17: fams.ERR_TOO_LONG, 18: fams.ERR_TRUNCATED, 19: fams.ERR_REPETITION,
}


def load_binding():
    """Das Modul ``transcribe_cpp`` oder None, wenn es fehlt oder die native
    Bibliothek nicht lädt (Import prüft Version und Header-Hash)."""
    try:
        import transcribe_cpp
    except Exception:   # noqa: BLE001 — ImportError, OSError, AbiError
        return None
    return transcribe_cpp


def read_wav_float(path):
    """16-kHz-Mono-16-Bit-WAV → array('f'); sonst None (kein Resampling)."""
    try:
        with wave.open(os.fspath(path), "rb") as w:
            if (w.getframerate() != 16000 or w.getnchannels() != 1
                    or w.getsampwidth() != 2):
                return None
            frames = w.readframes(w.getnframes())
    except (OSError, EOFError, wave.Error):
        return None
    pcm16 = array.array("h")
    pcm16.frombytes(frames)
    if sys.byteorder == "big":
        pcm16.byteswap()
    if not pcm16:
        return None
    return array.array("f", (s / 32768.0 for s in pcm16))


def _segments(result):
    return tuple({"start": s.t0_ms / 1000.0, "end": s.t1_ms / 1000.0,
                  "text": s.text.strip()}
                 for s in getattr(result, "segments", ()) or ()
                 if s.t1_ms >= s.t0_ms and (s.t0_ms or s.t1_ms))


def _words(result):
    return tuple({"start": w.t0_ms / 1000.0, "end": w.t1_ms / 1000.0,
                  "text": w.text.strip()}
                 for w in getattr(result, "words", ()) or ()
                 if w.t1_ms >= w.t0_ms)


class TranscribeCppPythonBackend:
    """Langlebiges Modell im Prozess; ohne Bindung oder Modell nicht gesund."""

    def __init__(self, model=None, family=None, *, compute="auto", threads=0,
                 vocabulary=None, timeout=120, binding=None):
        self.model = os.fspath(model) if model else None
        if compute not in COMPUTE_BACKENDS:
            raise ValueError(f"compute muss eines von {COMPUTE_BACKENDS} sein")
        self.compute = compute
        self.threads = int(threads or 0)
        self.vocabulary = vocabulary
        self.timeout = timeout
        self._binding = binding
        self._binding_checked = binding is not None
        name = family or fams.guess_family(self.model)
        self.family = fams.family(name) if name else None
        self.capabilities = self._capabilities()
        self._lock = threading.Lock()
        self._loaded = None
        self._session = None

    def _capabilities(self):
        fam = self.family
        if fam is None:
            return NO_CAPABILITIES
        return SpeechCapabilities(
            prompt=fam.vocabulary or fam.context_prompt,
            language_hint=fam.language != fams.LANG_AUTO_ONLY,
            streaming=fam.streaming, timestamps=fam.timestamps)

    @property
    def quant(self):
        return fams.quant_from_path(self.model)

    def binding(self):
        if not self._binding_checked:
            self._binding = load_binding()
            self._binding_checked = True
        return self._binding

    def health(self) -> bool:
        return bool(self.model and os.path.isfile(self.model)
                    and self.family is not None and self.binding() is not None)

    def warmup(self) -> bool:
        if not self.health():
            return False
        try:
            self._ensure_loaded()
        except Exception:   # noqa: BLE001
            return False
        return True

    def _ensure_loaded(self):
        if self._session is None:
            tc = self.binding()
            model = tc.Model(self.model, backend=self.compute)
            try:
                session = model.session(n_threads=self.threads)
            except Exception:
                model.close()
                raise
            self._loaded, self._session = model, session
        return self._session

    def close(self):
        with self._lock:
            model, self._loaded, self._session = self._loaded, None, None
            if model is not None:
                model.close()   # schließt auch die Session (Upstream :1046)

    def transcribe(self, pcm_or_wav, opts: TranscriptionOptions) -> SpeechResult:
        if not isinstance(pcm_or_wav, (str, os.PathLike)):
            return SpeechResult(text=None, error=fams.ERR_BAD_INPUT)
        if not self.model or self.family is None:
            return SpeechResult(text=None, error=fams.ERR_NO_MODEL)
        if self.binding() is None:
            return SpeechResult(text=None, error=fams.ERR_UNAVAILABLE)
        requested = opts.language_hint or getattr(opts.cfg, "language", None)
        language, notes = fams.resolve_language(self.family, requested)
        if self.family.max_audio_s:
            seconds = wav_seconds(pcm_or_wav)
            if seconds is not None and seconds > self.family.max_audio_s:
                return SpeechResult(text=None, language=language,
                                    error=fams.ERR_TOO_LONG, notes=notes)
        pcm = read_wav_float(pcm_or_wav)
        if pcm is None:
            return SpeechResult(text=None, language=language,
                                error=fams.ERR_AUDIO, notes=notes)
        kwargs = {"language": language, "timestamps": "auto"}
        if self.family.vocabulary:
            words = _vocabulary_words(self.vocabulary)
            if words:
                kwargs["vocabulary"] = words
        if self.family.context_prompt and opts.prompt_extra:
            kwargs["prompt"] = opts.prompt_extra
        timeout = getattr(opts, "timeout", None) or self.timeout
        with self._lock:
            try:
                session = self._ensure_loaded()
            except Exception as exc:   # noqa: BLE001
                return SpeechResult(text=None, language=language,
                                    error=self._error_for(exc, fams.ERR_MODEL_LOAD),
                                    notes=notes)
            # Frist: die Bibliothek bricht an der nächsten Chunk-/Decode-
            # Grenze ab (Session.cancel, Upstream :1415-1421).
            timer = threading.Timer(timeout, session.cancel)
            timer.daemon = True
            timer.start()
            try:
                result = session.run(pcm, **kwargs)
            except Exception as exc:   # noqa: BLE001
                error = self._error_for(exc, fams.ERR_BACKEND)
                partial = getattr(exc, "partial_result", None)
                if error in fams.PARTIAL_ERRORS and partial is not None \
                        and (partial.text or "").strip():
                    return SpeechResult(text=partial.text.strip(),
                                        segments=_segments(partial),
                                        words=_words(partial),
                                        language=language, error=error,
                                        notes=notes)
                return SpeechResult(text=None, language=language, error=error,
                                    notes=notes)
            finally:
                timer.cancel()
        return SpeechResult(text=(result.text or "").strip(),
                            segments=_segments(result), words=_words(result),
                            language=language, notes=notes)

    @staticmethod
    def _error_for(exc, default):
        return _STATUS_ERRORS.get(getattr(exc, "status", None), default)
