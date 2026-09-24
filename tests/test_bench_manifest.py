"""Tests für das Manifest-Format des Messrahmens (JSON/CSV, Scanner)."""
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bench import manifest  # noqa: E402


def write_wav(path, seconds=0.2, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def test_load_and_save_json_roundtrip(tmp_path):
    raw = {
        "version": 1,
        "note": "Test",
        "items": [{"id": "a", "path": "x/01.wav", "ref": "Hallo Welt.",
                   "language": "de", "category": "kurz", "source": "s",
                   "expect_words": ["Welt"]}],
        "open": ["eins"],
    }
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    data = manifest.load_manifest(path)
    assert data.items[0].expect_words == ["Welt"]
    out = tmp_path / "out.json"
    manifest.save_manifest(data, out)
    assert json.loads(out.read_text(encoding="utf-8")) == raw


def test_load_and_save_csv_roundtrip(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        "id,path,ref,language,category,source,expect_words\n"
        'a,x/01.wav,"Hallo, Welt.",de,kurz,s,Welt Fahrradklinge\n',
        encoding="utf-8")
    data = manifest.load_manifest(path)
    item = data.items[0]
    assert item.ref == "Hallo, Welt."
    assert item.language == "de"
    assert item.category == "kurz"
    assert item.expect_words == ["Welt", "Fahrradklinge"]
    out = tmp_path / "out.csv"
    manifest.save_manifest(data, out)
    again = manifest.load_manifest(out)
    assert again.items[0] == item
    assert again.note is None and again.open == []


def test_ref_none_means_open_and_empty_ref_runs(tmp_path):
    data = manifest.Manifest(items=[
        manifest.Item(id="ohne", path="x/01.wav", ref=None),
        manifest.Item(id="stille", path="x/02.wav", ref=""),
    ])
    lines = manifest.missing_reference_lines(data)
    assert len(lines) == 1 and "ohne" in lines[0]


def test_scan_samples_dir_reads_refs_and_applies_length_rule(tmp_path):
    write_wav(tmp_path / "01.wav", seconds=0.5)
    (tmp_path / "01.txt").write_text("Hallo Welt.\n", encoding="utf-8")
    write_wav(tmp_path / "02.wav", seconds=30.0)   # keine 02.txt
    items = manifest.scan_samples_dir(tmp_path)
    assert [i.category for i in items] == ["kurz", "lang"]
    assert items[0].ref == "Hallo Welt."
    assert items[0].language is None               # wird nicht geraten
    assert items[1].ref is None
    assert items[1].path == str(Path(tmp_path.name) / "02.wav")


def test_validate_manifest_reports_unknown_values_and_duplicates():
    problems = manifest.validate_manifest(manifest.Manifest(items=[
        manifest.Item(id="a", path="x.wav", language="fr"),
        manifest.Item(id="a", path="y.wav", category="geraeusch"),
        manifest.Item(id="", path=""),
    ]))
    assert len(problems) == 5
    assert any("Sprache" in p for p in problems)
    assert any("Kategorie" in p for p in problems)
    assert any("Doppelte" in p for p in problems)
    assert any("Pfad" in p for p in problems)


def test_shipped_samples_manifest_is_complete_and_valid():
    data = manifest.load_manifest(manifest.DEFAULT_MANIFEST)
    assert data.items, "vorgepacktes Manifest ohne Items"
    assert manifest.validate_manifest(data) == []
    for item in data.items:
        assert item.ref is not None, f"{item.id}: Referenz fehlt"
        assert not Path(item.path).is_absolute()
    # Das vorhandene Material: 7 echte + 2 synthetische Aufnahmen, de/en,
    # kurz/lang. Namens-/Stille-/Code-/Mixed-Fälle gibt es nicht und werden
    # als offene Punkte geführt, nicht erfunden.
    assert len(data.items) == 9
    assert {i.language for i in data.items} == {"de", "en"}
    assert {i.category for i in data.items} == {"kurz", "lang"}
    assert any("mixed" in o for o in data.open)
    assert any("namen" in o for o in data.open)
    assert any("stille" in o for o in data.open)
    assert any("code" in o for o in data.open)


def test_shipped_manifest_refs_match_the_recordings():
    """Referenztexte im Manifest = wortgetreue .txt-Inhalte, soweit lesbar."""
    data = manifest.load_manifest(manifest.DEFAULT_MANIFEST)
    import os
    src = os.environ.get("QUASSEL_BENCH_DATA_ROOT")
    if not src:
        import pytest
        pytest.skip("Aufnahmeverzeichnis nicht angegeben (QUASSEL_BENCH_DATA_ROOT)")
    for item in data.items:
        txt = (Path(src) / item.path).with_suffix(".txt")
        assert txt.read_text(encoding="utf-8").strip() == item.ref
