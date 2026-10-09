"""Trockenlauf des Messrahmens mit Fake-Backend — kein Netz, kein Server.

Belegt die Metriken des Messrahmens von der Manifest-Datei bis zum Report:
WER, sichtbare Zeichensetzungs-/Großschreibungsfehler, Wörterbuch-Treffer,
Halluzinationen auf Stille-Clips, Zeiten (Median/p95), Markdown und JSON.
"""
import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bench import run_bench                          # noqa: E402
from quassel.backends import (SpeechCapabilities,    # noqa: E402
                              SpeechResult)


def write_wav(path, seconds=0.2, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


class FakeBackend:
    """SpeechBackend-Vertrag ohne Netz: Namen → fester Ausgabetext."""
    capabilities = SpeechCapabilities(prompt=False, language_hint=False,
                                      streaming=False, timestamps=False)

    def __init__(self, answers):
        self.answers = dict(answers)
        self.calls = []

    def transcribe(self, wav, opts):
        self.calls.append((str(wav), opts))
        return SpeechResult(text=self.answers[Path(wav).name],
                            language=opts.language_hint)

    def health(self):
        return True

    def warmup(self):
        return True


def make_items(tmp_path):
    for name in ("01.wav", "02.wav", "03.wav", "04.wav", "05.wav"):
        write_wav(tmp_path / name)
    return [
        run_bench.manifest_mod.Item(
            id="de-kurz-01", path="01.wav", ref="Die Rechnung beläuft sich "
            "auf 42 Euro.", language="de", category="kurz"),
        run_bench.manifest_mod.Item(
            id="en-kurz-02", path="02.wav", ref="hello world",
            language="en", category="kurz"),
        run_bench.manifest_mod.Item(
            id="de-namen-03", path="03.wav", ref="Henrike arbeitet an Quassel.",
            language="de", category="namen",
            expect_words=["Henrike", "Quassel", "Rostock"]),
        run_bench.manifest_mod.Item(
            id="de-kurz-04", path="04.wav", ref="Hallo Welt",
            language="de", category="kurz"),
        run_bench.manifest_mod.Item(
            id="stille-05", path="05.wav", ref="", language="de",
            category="stille"),
    ]


ANSWERS = {
    "01.wav": "Die Rechnung beläuft sich auf 42 Euro.",
    "02.wav": "hello big world",
    "03.wav": "Henrike arbeitet an Quassel",
    "04.wav": "hallo Welt",
    "05.wav": "Danke fürs Zuschauen.",
}


def test_dry_run_proves_every_metric():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        items = make_items(root)
        section = run_bench.run_backend_section(
            "fake", FakeBackend(ANSWERS), items, root, runs=2, timeout=5)
        by = {r["label"]: r for r in section["results"]}

        # WER normalisiert wie Open ASR:
        assert by["de-kurz-01"]["wer"] == 0.0
        assert by["en-kurz-02"]["wer"] == 0.5          # Einfügung: 1/2
        assert by["de-namen-03"]["wer"] == 0.0          # Satzzeichen wegnormalisiert
        assert by["de-kurz-04"]["wer"] == 0.0
        assert by["stille-05"]["wer"] == 1.0            # leere Referenz + Ausgabe

        # Sichtbare Zeichensetzung und Großschreibung:
        assert by["de-namen-03"]["punct_err_rate"] == 0.25
        assert by["de-namen-03"]["cap_err_rate"] == 0.0
        assert by["de-kurz-04"]["cap_err_rate"] == 0.5
        assert by["de-kurz-01"]["punct_err_rate"] == 0.0

        # Wörterbuch-Trefferquote (ganzes Wort, exakt):
        assert (by["de-namen-03"]["dict_hits"],
                by["de-namen-03"]["dict_total"]) == (2, 3)
        assert by["de-namen-03"]["dict_rate"] == 2 / 3
        assert by["de-kurz-01"]["dict_rate"] is None    # keine Liste, n/a

        # Halluzination auf dem Stille-Clip:
        assert by["stille-05"]["hallucinated"] is True
        assert by["stille-05"]["known_artifact"] is True

        # Zeiten je Clip mit Median und p95:
        for r in section["results"]:
            assert r["times_s"] and len(r["times_s"]) == 2
            assert r["median_s"] is not None and r["p95_s"] >= r["median_s"]
            assert r["hyps_identical"] is True
            assert r["server_median_s"] is None         # Fake-Backend ohne HTTP

        summary = section["summary"]
        assert summary["overall"]["clips"] == 5
        assert summary["overall"]["wer_mean"] == 0.3
        assert summary["overall"]["hallucination_rate"] == 1.0
        assert summary["by_language"]["de"]["clips"] == 4
        assert summary["by_language"]["de"]["wer_mean"] == 0.25
        assert summary["by_language"]["en"]["wer_mean"] == 0.5
        assert summary["by_category"]["namen"]["dict_rate_mean"] == 2 / 3

        # Gesamtzeit p95 über alle Clip-Läufe vorhanden:
        assert summary["overall"]["total_time_s"]["p95"] is not None

        report = {"meta": run_bench.build_meta(
            SimpleNamespace(root=str(root)), "manifest.json"),
            "backends": [section], "open": []}
        json.dumps(report, ensure_ascii=False)          # JSON-Report serialisierbar
        markdown = run_bench.render_markdown(report)
        assert "| Clip |" in markdown and "WER" in markdown
        assert "Danke fürs Zuschauen." in markdown


def test_backend_with_failed_health_is_skipped_cleanly(tmp_path):
    class DeadBackend(FakeBackend):
        def health(self):
            return False

        def transcribe(self, wav, opts):
            raise AssertionError("nach health()=False nie aufgerufen")

    report = run_bench.report_for_backends(
        [("dead", DeadBackend({}), False)], [], root=tmp_path,
        meta={"date": "x", "platform": "test", "python": "test",
              "repo_commit": None, "manifest": "m", "root": str(tmp_path)},
        open_lines=["ein offener Punkt"], runs=1, timeout=1)
    section = report["backends"][0]
    assert section["skipped"] is True
    assert section["results"] == []
    assert report["open"] == ["ein offener Punkt"]
    markdown = run_bench.render_markdown(report)
    assert "Übersprungen" in markdown and "kein echter Lauf" in markdown


def test_select_backends_without_binary_skips_the_stub():
    args = SimpleNamespace(backend="whisper", transcribe_cpp=None)
    names = [n for n, _, _ in run_bench.select_backends(args)]
    assert names == ["whisper-server"]
    args = SimpleNamespace(backend="all", transcribe_cpp=None)
    names = [n for n, _, _ in run_bench.select_backends(args)]
    assert names == ["whisper-server"]        # Stub ohne Pfad übersprungen
    args = SimpleNamespace(backend="transcribe-cpp",
                           transcribe_cpp="/opt/transcribe-cli")
    names = [n for n, _, _ in run_bench.select_backends(args)]
    assert names == ["transcribe-cpp"]


def test_main_runs_offline_when_no_backend_is_selectable(tmp_path, capsys):
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(json.dumps({
        "version": 1,
        "items": [{"id": "fehlt", "path": "x/01.wav", "ref": "Hallo",
                   "language": "de", "category": "kurz"}],
    }), encoding="utf-8")
    exit_code = run_bench.main([str(manifest_path), "--root", str(tmp_path),
                                "--backend", "transcribe-cpp"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Aufnahme fehlt" in out            # offener Punkt, sauber übersprungen
    assert "transcribe-cpp" not in out        # kein Abschnitt ohne gewähltes Backend


def test_dry_run_over_a_json_manifest(tmp_path, capsys):
    items = make_items(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_mod = run_bench.manifest_mod
    manifest_mod.save_manifest(manifest_mod.Manifest(items=items), manifest_path)
    # Fake-Backend kann über main() nicht gewählt werden; der Beweis läuft über
    # run_backend_section (oben). Hier nur: Manifest aus Datei wird korrekt
    # gelesen und die offene Stille-Referenz wird nicht als Fehler gemeldet.
    data = manifest_mod.load_manifest(manifest_path)
    assert len(data.items) == 5
    assert data.items[4].ref == ""            # absichtlich leer = Stille


def test_server_time_recorder_passes_every_argument_through(monkeypatch):
    """whisperclient._post_multipart bekam ein viertes Argument (errors). Der
    Zeitmesser nahm nur drei: jede Turbo-Anfrage der Latenzmessung vom 09.10.
    scheiterte mit TypeError, die Zeiten standen auf 0,000 s."""
    import quassel.whisperclient as wc
    seen = []

    def original(body, content_type, timeout, errors=None):
        seen.append((body, content_type, timeout, errors))
        return "text"
    monkeypatch.setattr(wc, "_post_multipart", original)
    recorder = []
    with run_bench._record_server_time(recorder):
        errors = []
        assert wc._post_multipart(b"x", "multipart/form-data", 5, errors) == "text"
    assert seen == [(b"x", "multipart/form-data", 5, [])]
    assert len(recorder) == 1 and recorder[0][1] is True
    assert wc._post_multipart is original
