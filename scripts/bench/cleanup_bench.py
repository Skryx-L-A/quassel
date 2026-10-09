"""Measure the offline cleanup corpus against an explicitly chosen local /v1 API.

No audio, GUI, paste, model start or download. This script is not run by tests.
It prints aggregates only; transcripts and generated text are never logged,
except that --show-unsafe writes the synthetic corpus text of unsafe cases to --cases.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from quassel import ai  # noqa: E402
from quassel.cleanup import cleanup  # noqa: E402


CORPUS = Path(__file__).resolve().parents[2] / "tests" / "data" / "cleanup_cases.jsonl"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def run(endpoint: str, model: str, level: str, timeout: float,
        cases_path: Path | None = None, show_unsafe: bool = False) -> dict:
    if not ai.is_loopback(endpoint) or ai.endpoint_warning(endpoint) == "invalid":
        raise ValueError("endpoint must be an explicit loopback URL")
    cases = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()]
    latencies = []
    reasons = Counter()
    by_language = Counter()
    deviations = 0
    unsafe_changes = 0
    accepted = 0
    used_llm = 0
    case_rows = []
    for case in cases:
        cfg = SimpleNamespace(cleanup_enabled=True, cleanup_terms=case["terms"],
                              cleanup_replacements={}, ai_model=model,
                              ai_endpoint=endpoint, ai_timeout=timeout)
        start = time.monotonic()
        result = cleanup(case["raw"], level, cfg)
        used_llm += result.used_llm
        latencies.append(time.monotonic() - start)
        by_language[case["language"]] += 1
        if result.fallback_reason:
            reasons[result.fallback_reason] += 1
        else:
            accepted += 1
        if result.text != case["expected_rule"]:
            deviations += 1
        unsafe = (case["expected_guard"] == case["raw"]
                  and result.text != case["expected_rule"])
        if unsafe:
            unsafe_changes += 1
        if cases_path is not None:
            case_rows.append({"id": case["id"], "level": level,
                              "used_llm": result.used_llm,
                              "fallback_reason": result.fallback_reason,
                              "status": "unsicher" if unsafe else "ok"})
            if unsafe and show_unsafe:
                # Nur das synthetische Korpus, nie ein echtes Diktat: die
                # abweichende Ausgabe zeigt, was der Schutz übersehen hat.
                case_rows[-1].update(raw=case["raw"], output=result.text,
                                     expected=case["expected_rule"])
    if cases_path is not None:
        cases_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                      for row in case_rows), encoding="utf-8")
    return {
        "cases": len(cases), "by_language": dict(by_language), "accepted": accepted,
        "used_llm": used_llm, "used_llm_rate": used_llm / len(cases),
        "fallback": sum(reasons.values()), "fallback_rate": sum(reasons.values()) / len(cases),
        "fallback_reasons": dict(reasons), "deviations_from_rule": deviations,
        "unsafe_changes": unsafe_changes,
        "latency_seconds": {"mean": round(statistics.mean(latencies), 3),
                            "p50": round(percentile(latencies, 0.5), 3),
                            "p95": round(percentile(latencies, 0.95), 3)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="explicit local OpenAI-compatible base URL")
    parser.add_argument("--model", required=True)
    parser.add_argument("--level", choices=("mittel", "stark"), default="mittel")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--cases", type=Path, help="optional JSONL with per-case metadata only")
    parser.add_argument("--show-unsafe", action="store_true",
                        help="also write raw, output and expected text of unsafe cases to --cases "
                             "(synthetic corpus only)")
    args = parser.parse_args()
    if not 0 < args.timeout <= 3600:
        parser.error("--timeout must be between 0 and 3600 seconds")
    try:
        summary = run(args.endpoint, args.model, args.level, args.timeout, args.cases,
                      show_unsafe=args.show_unsafe)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if summary["fallback"] == summary["cases"] and not summary["accepted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
