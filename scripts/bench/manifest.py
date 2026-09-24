"""Manifest-Format für STT-Testaufnahmen (JSON oder CSV).

Ein Manifest beschreibt die Testaufnahmen des Messrahmens: Pfad, Referenztext,
Sprache (de/en/mixed) und Kategorie (kurz/lang/namen/stille/code). Fehlende
Referenzen werden als offene Punkte geführt und nicht erfunden; ``ref=None``
heißt Referenz fehlt (offen), ``ref=""`` heißt absichtlich leer (Stille-Clip).

JSON-Form (scripts/bench/samples_manifest.json ist das vorhandene Material):

    {
      "version": 1,
      "note": "...",
      "items": [
        {"id": "de-kurz-01", "path": "bench_samples/01.wav", "ref": "...",
         "language": "de", "category": "kurz", "source": "...",
         "expect_words": []}
      ],
      "open": ["..."]
    }

CSV-Form: Spalten id,path,ref,language,category,source,expect_words;
``expect_words`` ist eine durch Leerzeichen getrennte Liste. ``note`` und die
offenen Punkte stehen nur in der JSON-Form.

Pfade sind relativ zu einem Datenursprung (beim Lauf ``--root``, Vorgabe der
Repo-Ursprung), weil die Aufnahmen gitignored im Datenverzeichnis liegen.
"""
from __future__ import annotations

import csv
import json
import sys
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    from benchmark_stt_mac import wav_duration_seconds as _raw_wav_duration
except ImportError:  # direkter Aufruf ohne scripts/ im Pfad
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from benchmark_stt_mac import wav_duration_seconds as _raw_wav_duration

VERSION = 1
LANGUAGES = ("de", "en", "mixed")
CATEGORIES = ("kurz", "lang", "namen", "stille", "code")
# Mechanische Kategorieregel für gescannte Aufnahmen: ab 30 s zählt ein Clip
# als "lang", darunter als "kurz". Kuratierte Manifeste dürfen abweichen.
LONG_CLIP_SECONDS = 30.0
CSV_COLUMNS = ("id", "path", "ref", "language", "category", "source",
               "expect_words")
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "samples_manifest.json"


def wav_duration_seconds(path):
    """Dauer einer Aufnahme in Sekunden; None bei unlesbaren Dateien."""
    try:
        return _raw_wav_duration(Path(path))
    except (OSError, wave.Error, EOFError):
        return None


@dataclass
class Item:
    id: str
    path: str
    ref: str | None = None
    language: str | None = None
    category: str | None = None
    source: str | None = None
    expect_words: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    version: int = VERSION
    note: str | None = None
    items: list[Item] = field(default_factory=list)
    open: list[str] = field(default_factory=list)


def _item_from_dict(raw):
    return Item(
        id=str(raw.get("id", "")).strip(),
        path=str(raw.get("path", "")).strip(),
        ref=(str(raw["ref"]) if raw.get("ref") is not None else None),
        language=(str(raw["language"]).strip() or None
                  if raw.get("language") is not None else None),
        category=(str(raw["category"]).strip() or None
                  if raw.get("category") is not None else None),
        source=(str(raw["source"]) if raw.get("source") is not None else None),
        expect_words=[str(w) for w in raw.get("expect_words") or []],
    )


def load_manifest(path) -> Manifest:
    path = Path(path)
    if path.suffix.casefold() == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        items = []
        for row in rows:
            raw = {k: (v if v not in (None, "") else None)
                   for k, v in row.items() if k in CSV_COLUMNS}
            raw["expect_words"] = (raw.get("expect_words") or "").split()
            items.append(_item_from_dict(raw))
        return Manifest(items=items)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):          # flache Item-Liste zulassen
        data = {"items": data}
    return Manifest(
        version=int(data.get("version", VERSION)),
        note=data.get("note"),
        items=[_item_from_dict(raw) for raw in data.get("items", [])],
        open=[str(o) for o in data.get("open", [])],
    )


def save_manifest(data: Manifest, path, fmt=None):
    """Manifest schreiben; das Format folgt sonst der Dateiendung."""
    path = Path(path)
    fmt = (fmt or path.suffix.casefold().lstrip(".")).casefold()
    if fmt == "csv":
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for item in data.items:
                d = asdict(item)
                d["expect_words"] = " ".join(item.expect_words)
                d = {k: d.get(k) for k in CSV_COLUMNS}
                writer.writerow({k: ("" if v is None else v)
                                 for k, v in d.items()})
        return
    if fmt != "json":
        raise ValueError(f"Unbekanntes Manifest-Format: {fmt!r}")
    path.write_text(
        json.dumps(asdict(data), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")


def scan_samples_dir(directory, prefix=""):
    """Vorhandene Aufnahmen eines Verzeichnisses einlesen.

    Referenztexte kommen aus gleichnamigen .txt-Dateien; fehlen sie, wird
    ref=None gesetzt und die Referenz ist ein offener Punkt. Sprache wird
    nicht geraten (None); die Kategorie folgt der Längenregel.
    """
    directory = Path(directory)
    items = []
    for wav in sorted(directory.glob("*.wav")):
        txt = wav.with_suffix(".txt")
        ref = txt.read_text(encoding="utf-8").strip() if txt.exists() else None
        duration = wav_duration_seconds(wav) or 0.0
        items.append(Item(
            id=f"{prefix}{wav.stem}",
            path=str(Path(wav.parent.name) / wav.name),
            ref=ref,
            language=None,
            category="lang" if duration >= LONG_CLIP_SECONDS else "kurz",
            source=f"eingescannt aus {directory.name}/",
        ))
    return items


def missing_reference_lines(data: Manifest):
    """Offene Punkte: fehlende Referenzen plus die offene Liste des Manifests."""
    lines = [f"{item.id} ({item.path}): keine Referenz — als offen geführt"
             for item in data.items if item.ref is None]
    return lines + list(data.open)


def validate_manifest(data: Manifest):
    """Liste von Problemen (leer = ok): Werte, Doppel-IDs, leere Pfade."""
    problems = []
    seen = set()
    for item in data.items:
        if not item.id:
            problems.append(f"Item ohne id: {item.path}")
        if item.id in seen:
            problems.append(f"Doppelte id: {item.id}")
        seen.add(item.id)
        if not item.path:
            problems.append(f"{item.id or '?'}: Pfad fehlt")
        if item.language is not None and item.language not in LANGUAGES:
            problems.append(f"{item.id}: unbekannte Sprache {item.language!r}")
        if item.category is not None and item.category not in CATEGORIES:
            problems.append(f"{item.id}: unbekannte Kategorie {item.category!r}")
    return problems
