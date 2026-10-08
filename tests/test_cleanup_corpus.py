"""Two-language offline corpus for rules and guarded model responses."""

from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace

from quassel import ai
from quassel.cleanup import cleanup


CASES = Path(__file__).parent / "data" / "cleanup_cases.jsonl"


def test_cleanup_corpus(monkeypatch):
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()]
    counts = Counter(case["language"] for case in cases)
    assert counts["de"] >= 100 and counts["en"] >= 100
    assert len({(case["language"], case["raw"]) for case in cases}) == len(cases)
    required = {"filler_repeat", "self_correction", "number", "url", "email",
                "term", "code", "question", "injection", "currency"}
    for language in ("de", "en"):
        assert {case["category"] for case in cases if case["language"] == language} == required
    for case in cases:
        cfg = SimpleNamespace(cleanup_enabled=True, cleanup_terms=case["terms"],
                              cleanup_replacements={}, ai_model="offline-fake",
                              ai_endpoint="http://127.0.0.1:9", ai_timeout=0.1)
        light = cleanup(case["raw"], "leicht", cfg)
        assert light.text == case["expected_rule"], case
        assert light.fallback_reason is None, case
        monkeypatch.setattr(ai, "chat_cleanup", lambda *a, **kw: (case["candidate"], None))
        guarded = cleanup(case["raw"], "mittel", cfg)
        assert guarded.text == case["expected_guard"], case
        if case["expected_guard"] == case["raw"] and case["candidate"] != case["raw"]:
            assert guarded.fallback_reason is not None, case
