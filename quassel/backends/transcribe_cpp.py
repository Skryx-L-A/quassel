"""Adapter für ``transcribe-cli`` aus transcribe.cpp (Messrahmen, nicht im Diktatpfad).

CLI-Vertrag aus dem Upstream-Tag (tcpp_families.UPSTREAM_TAG), Datei
``examples/cli/main.cpp``:

- ``-m PATH`` Modell (:458), ``-l ISO`` Sprache (:464), ``--threads N`` (:561),
  ``--backend auto|cpu|cpu_accel|metal|vulkan|cuda|rocm`` (:586),
  ``--device N`` (:610), ``--vocabulary-file P`` ein Begriff je Zeile
  (:491-527), ``-q`` ohne Bibliothekslog (:540).
- Maschinenlesbare Ausgabe gibt es nur im Batch-Modus: ``--batch LISTE
  --batch-jsonl`` (:639-646). Je Datei eine JSON-Zeile mit ``file``, ``text``,
  optional ``segments`` (``t0_ms``/``t1_ms``/``text``), ``error`` und
  Zeiten (:1253-1273); davor eine Kopfzeile ``{"type":"batch_header"}``
  (:1001-1007). Ein Modellladefehler schreibt ``model load: <status>`` nach
  stderr und endet ohne JSON-Zeile (:929-932).
- Teilergebnisse bei "output truncated"/"output repetition" behalten den
  Text und tragen das Fehlerfeld (:1223-1229, :1259-1263).

Es gibt keinen Server- oder Stdin-Modus: die Batch-Liste wird vor dem ersten
Lauf vollständig gelesen (:893-911). Dieser Adapter startet deshalb je Clip
einen Prozess; der Modell-Ladevorgang gehört zu jeder Messung dazu.

Datenschutz: Kontexttext (``prompt_extra``, beim Streaming bereits diktierter
Text) käme nur als ``--prompt TEXT`` in die Befehlszeile und wäre damit für
andere lokale Nutzer in der Prozessliste sichtbar. Der Adapter übergibt ihn
deshalb nie. Das Wörterbuch geht über eine private Temp-Datei
(``--vocabulary-file``). Weder stdout noch stderr werden geloggt.
"""
import json
import os
import shutil
import subprocess
import tempfile
import wave

from . import tcpp_families as fams
from .base import SpeechCapabilities, SpeechResult, TranscriptionOptions
from ..whisperclient import NOWIN

COMPUTE_BACKENDS = ("auto", "cpu", "cpu_accel", "metal", "vulkan", "cuda", "rocm")
NO_CAPABILITIES = SpeechCapabilities(prompt=False, language_hint=False,
                                     streaming=False, timestamps=False)


def capabilities_for(fam):
    """Fähigkeiten der Modellfamilie in Quassels Vertrag.

    ``prompt`` heißt hier: das Wörterbuch wirkt (Whisper, Qwen3-ASR).
    ``streaming`` beschreibt die Familie; der CLI-Adapter selbst arbeitet je
    Clip und speist nie live.
    """
    if fam is None:
        return NO_CAPABILITIES
    return SpeechCapabilities(
        prompt=fam.vocabulary,
        language_hint=fam.language != fams.LANG_AUTO_ONLY,
        streaming=fam.streaming,
        timestamps=fam.timestamps)


def wav_seconds(path):
    try:
        with wave.open(os.fspath(path), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / rate if rate else None
    except (OSError, EOFError, wave.Error):
        return None


def segments_from_rows(rows):
    """Upstream-Zeilen (ms) → Quassels Segmentform (Sekunden, wie
    transcribe_file._offset_segments sie erwartet)."""
    out = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        t0, t1, text = row.get("t0_ms"), row.get("t1_ms"), row.get("text")
        if not isinstance(t0, (int, float)) or not isinstance(t1, (int, float)) \
                or not isinstance(text, str) or t1 < t0:
            continue
        out.append({"start": t0 / 1000.0, "end": t1 / 1000.0,
                    "text": text.strip()})
    return tuple(out)


def _vocabulary_words(source):
    if source is None:
        return []
    words = source() if callable(source) else source
    clean = []
    for word in words or ():
        word = str(word).replace("\r", " ").replace("\n", " ").strip()
        if word:
            clean.append(word)
    return clean


def _write_private(path, text):
    # 0600 zusätzlich zum 0700-Ordner von mkdtemp: Wörterbuch ist Nutzerdatum.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


class TranscribeCppBackend:
    """Ein Prozess je Clip; ohne Binary, Modell oder Familie nicht gesund."""

    def __init__(self, binary=None, timeout=120, *, model=None, family=None,
                 compute="auto", device=None, threads=0, vocabulary=None):
        self.binary = os.fspath(binary) if binary else None
        self.model = os.fspath(model) if model else None
        self.timeout = timeout
        if compute not in COMPUTE_BACKENDS:
            raise ValueError(f"compute muss eines von {COMPUTE_BACKENDS} sein")
        self.compute = compute
        self.device = device
        self.threads = int(threads or 0)
        self.vocabulary = vocabulary
        name = family or fams.guess_family(self.model)
        self.family = fams.family(name) if name else None
        self.capabilities = capabilities_for(self.family)

    @property
    def quant(self):
        return fams.quant_from_path(self.model)

    def build_argv(self, list_path, language=None, vocabulary_path=None):
        argv = [self.binary, "-m", self.model, "--batch", os.fspath(list_path),
                "--batch-jsonl", "-q", "--backend", self.compute]
        if language:
            argv += ["-l", language]
        if self.threads > 0:
            argv += ["--threads", str(self.threads)]
        if self.device is not None:
            argv += ["--device", str(int(self.device))]
        if vocabulary_path:
            argv += ["--vocabulary-file", os.fspath(vocabulary_path)]
        return argv

    def transcribe(self, pcm_or_wav, opts: TranscriptionOptions) -> SpeechResult:
        # Kein Audioformat-Raten: nur echte Dateipfade, kein Raw-PCM.
        if not self.binary:
            return SpeechResult(text=None, error=fams.ERR_NO_BINARY)
        if not isinstance(pcm_or_wav, (str, os.PathLike)):
            return SpeechResult(text=None, error=fams.ERR_BAD_INPUT)
        if not self.model or self.family is None:
            return SpeechResult(text=None, error=fams.ERR_NO_MODEL)
        wav = os.path.abspath(os.fspath(pcm_or_wav))
        if "\n" in wav or "\r" in wav:      # Batch-Liste ist zeilenbasiert
            return SpeechResult(text=None, error=fams.ERR_BAD_INPUT)
        requested = opts.language_hint or getattr(opts.cfg, "language", None)
        language, notes = fams.resolve_language(self.family, requested)
        if self.family.max_audio_s:
            seconds = wav_seconds(wav)
            if seconds is not None and seconds > self.family.max_audio_s:
                return SpeechResult(text=None, language=language,
                                    error=fams.ERR_TOO_LONG, notes=notes)
        words = _vocabulary_words(self.vocabulary) if self.family.vocabulary else []
        timeout = getattr(opts, "timeout", None) or self.timeout
        tmp = tempfile.mkdtemp(prefix="quassel-tcpp-")
        try:
            list_path = _write_private(os.path.join(tmp, "clips.list"),
                                       wav + "\n")
            vocab_path = None
            if words:
                vocab_path = _write_private(os.path.join(tmp, "vocabulary.txt"),
                                            "\n".join(words) + "\n")
            argv = self.build_argv(list_path, language, vocab_path)
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", check=False, timeout=timeout, **NOWIN)
            except subprocess.TimeoutExpired:
                return SpeechResult(text=None, language=language,
                                    error=fams.ERR_TIMEOUT, notes=notes)
            except OSError:
                return SpeechResult(text=None, language=language,
                                    error=fams.ERR_SPAWN, notes=notes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return self._parse(proc, language, notes)

    @staticmethod
    def _parse(proc, language, notes):
        record = None
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "file" in row:
                record = row
                break
        if record is None:
            error = fams.ERR_OUTPUT
            if proc.returncode != 0:
                lines = [l for l in (proc.stderr or "").splitlines() if l.strip()]
                error = fams.classify_status(lines[-1]) if lines else fams.ERR_EXIT
            return SpeechResult(text=None, language=language, error=error,
                                notes=notes)
        text = record.get("text")
        text = text.strip() if isinstance(text, str) else None
        segments = segments_from_rows(record.get("segments"))
        status = record.get("error")
        if status:
            error = fams.classify_status(status)
            keep = error in fams.PARTIAL_ERRORS and bool(text)
            return SpeechResult(text=text if keep else None,
                                segments=segments if keep else (),
                                language=language, error=error, notes=notes)
        return SpeechResult(text=text, segments=segments, language=language,
                            notes=notes)

    def health(self) -> bool:
        return bool(self.binary and os.path.isfile(self.binary)
                    and os.access(self.binary, os.X_OK)
                    and self.model and os.path.isfile(self.model)
                    and self.family is not None)

    def warmup(self) -> bool:
        # Je Clip ein neuer Prozess: es gibt nichts vorzuwärmen.
        return False
