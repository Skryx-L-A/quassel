"""Benchmark metadata stays independent of synthetic transcript contents."""
import json

from quassel.cleanup import CleanupResult
from scripts.bench import cleanup_bench


def test_case_report_and_used_llm_rate(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    rows = [
        {"id": "C001", "language": "en", "raw": "Um, private alpha.",
         "expected_rule": "private alpha.", "expected_guard": "Um, private alpha.", "terms": []},
        {"id": "C002", "language": "en", "raw": "private beta?",
         "expected_rule": "private beta?", "expected_guard": "private beta?", "terms": []},
    ]
    corpus.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(cleanup_bench, "CORPUS", corpus)
    results = iter([CleanupResult("private alpha.", used_llm=True),
                    CleanupResult("private gamma?", used_llm=False,
                                  fallback_reason="processing_error")])
    monkeypatch.setattr(cleanup_bench, "cleanup", lambda *a, **k: next(results))
    report = tmp_path / "cases.jsonl"
    summary = cleanup_bench.run("http://127.0.0.1:9", "fake", "mittel", 1, report)

    assert summary["used_llm"] == 1 and summary["used_llm_rate"] == 0.5
    assert summary["unsafe_changes"] == 1
    assert [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()] == [
        {"id": "C001", "level": "mittel", "used_llm": True,
         "fallback_reason": None, "status": "ok"},
        {"id": "C002", "level": "mittel", "used_llm": False,
         "fallback_reason": "processing_error", "status": "unsicher"},
    ]
    assert "private" not in report.read_text(encoding="utf-8")


def test_show_unsafe_adds_corpus_text_only_for_unsafe_cases(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    rows = [
        {"id": "C001", "language": "de", "raw": "Öffne https://example.com/a.",
         "expected_rule": "Öffne https://example.com/a.",
         "expected_guard": "Öffne https://example.com/a.", "terms": []},
        {"id": "C002", "language": "de", "raw": "Ähm, ja.", "expected_rule": "ja.",
         "expected_guard": "ja.", "terms": []},
    ]
    corpus.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(cleanup_bench, "CORPUS", corpus)
    results = iter([CleanupResult("Öffnen Sie https://example.com/a.", used_llm=True),
                    CleanupResult("ja.", used_llm=True)])
    monkeypatch.setattr(cleanup_bench, "cleanup", lambda *a, **k: next(results))
    report = tmp_path / "cases.jsonl"
    cleanup_bench.run("http://127.0.0.1:9", "fake", "mittel", 1, report, show_unsafe=True)
    first, second = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert first["status"] == "unsicher"
    assert first["output"] == "Öffnen Sie https://example.com/a."
    assert first["raw"] == "Öffne https://example.com/a."
    assert "output" not in second and "raw" not in second


def test_decider_address_reaches_the_cleanup_config(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"id": "C001", "language": "en", "raw": "a", "expected_rule": "a",
                                  "expected_guard": "a", "terms": []}) + "\n", encoding="utf-8")
    monkeypatch.setattr(cleanup_bench, "CORPUS", corpus)
    seen = []
    monkeypatch.setattr(cleanup_bench, "cleanup",
                        lambda raw, level, cfg: seen.append(cfg) or CleanupResult(raw))
    cleanup_bench.run("http://127.0.0.1:9", "fake", "stark", 1,
                      decider_url="http://127.0.0.1:8095")
    assert seen[0].cleanup_decider_url == "http://127.0.0.1:8095"
    cleanup_bench.run("http://127.0.0.1:9", "fake", "stark", 1)
    assert seen[1].cleanup_decider_url == ""


def test_decider_calls_are_counted_and_rejections_shown(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    rows = [{"id": f"C00{i}", "language": "en", "raw": f"raw {i}", "expected_rule": f"raw {i}",
             "expected_guard": f"raw {i}", "terms": []} for i in (1, 2, 3)]
    corpus.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(cleanup_bench, "CORPUS", corpus)
    verdicts = iter([True, False])

    def fake_meaning(*args, **kwargs):
        return next(verdicts)

    def fake_cleanup(raw, level, cfg):
        if raw == "raw 1":
            return CleanupResult(raw, used_llm=True)
        from quassel import decider
        ok = decider.same_meaning(cfg.cleanup_decider_url, raw, "changed", timeout=1)
        if ok:
            return CleanupResult(raw, used_llm=True)
        return CleanupResult(raw, used_llm=True, fallback_reason="decider_meaning")

    monkeypatch.setattr(cleanup_bench, "cleanup", fake_cleanup)
    monkeypatch.setattr(cleanup_bench.decider, "same_meaning", fake_meaning)
    report = tmp_path / "cases.jsonl"
    summary = cleanup_bench.run("http://127.0.0.1:9", "fake", "stark", 1, report,
                                show_unsafe=True, decider_url="http://127.0.0.1:8095")
    assert summary["decider"]["asked"] == 2
    assert summary["decider"]["rejected"] == 1 and summary["decider"]["unavailable"] == 0
    assert set(summary["decider"]["latency_seconds"]) == {"p50", "p95"}
    lines = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert [row["decider_asked"] for row in lines] == [False, True, True]
    assert "raw" not in lines[1] and lines[2]["raw"] == "raw 3"
    assert lines[2]["rejected_output"] == "changed"
    assert cleanup_bench.decider.same_meaning is fake_meaning
    monkeypatch.setattr(cleanup_bench, "cleanup", lambda raw, level, cfg: CleanupResult(raw))
    plain = cleanup_bench.run("http://127.0.0.1:9", "fake", "stark", 1)
    assert "decider" not in plain
