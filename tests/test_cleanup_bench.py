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
