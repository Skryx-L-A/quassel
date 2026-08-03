#!/usr/bin/env python3
"""AUFTRAG 1: sichere audio_ctx-Schwelle zwischen 10 und 20s, mit echtem
(nicht verkettetem) Material aus bench_samples/07.wav, feiner abgetastet
als die Vorgaengermessung, und zusaetzlichen Werten ueber 768 hinaus.
Server einmal gestartet mit Produktionsflags (-t 8 -bs 5 --vad), audio_ctx
nur als Anfragefeld variiert (beam_size bleibt bei 5, Produktionswert)."""
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (URL, infer, load1, port_free, start_server,  # noqa: E402
                     stop_server, word_error_rate)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "<repo>"
RUNS = 5
AC_VALUES = [None, 768, 1000, 1200]


def load_items():
    items = []
    items.append({
        "label": "kurz-5.8s", "path": REPO + "/bench_samples/01.wav",
        "ref": open(REPO + "/bench_samples/01.txt", encoding="utf-8").read().strip(),
        "duration_s": 5.805, "note": "echte Aufnahme, bench_samples/01",
    })
    segs = json.load(open(os.path.join(HERE, "segments_manifest.json"), encoding="utf-8"))
    for s in segs:
        items.append({
            "label": "%05.2fs" % s["duration_s"], "path": s["path"], "ref": s["ref"],
            "duration_s": s["duration_s"], "note": s["source"],
        })
    items.append({
        "label": "lang-48.1s", "path": REPO + "/bench_samples/07.wav",
        "ref": open(REPO + "/bench_samples/07.txt", encoding="utf-8").read().strip(),
        "duration_s": 48.14, "note": "echte Aufnahme, vollstaendig, bench_samples/07 (Kontrolle)",
    })
    return items


def run_point(item, ac):
    times, hyps, rcs = [], [], []
    for _ in range(RUNS):
        el, hyp, rc = infer(item["path"], audio_ctx=ac)
        times.append(el)
        hyps.append(hyp)
        rcs.append(rc)
    med = statistics.median(times)
    outlier = (max(times) / max(min(times), 1e-6)) > 3.0
    result = {
        "label": item["label"], "duration_s": item["duration_s"], "audio_ctx": ac,
        "reach_s": (ac / 1500.0 * 30.0) if ac else None,
        "times_s": times, "median_s": med, "min_s": min(times), "max_s": max(times),
        "outlier_flag": outlier, "returncodes": rcs,
        "hyp": hyps[0], "wer": word_error_rate(item["ref"], hyps[0]),
        "hyps_identical": len(set(hyps)) == 1,
        "note": item["note"], "load1_before": None,
    }
    return result


def main():
    if not port_free():
        print("Port belegt, Abbruch"); return 1
    items = load_items()
    out = []
    p, log = start_server()
    print("Server oben, load1=%.2f" % load1())
    try:
        # kurzer Warmlauf, zaehlt nicht in die Messung
        infer(items[0]["path"])
        for ac in AC_VALUES:
            print("== audio_ctx=%s ==" % ac)
            for item in items:
                l0 = load1()
                r = run_point(item, ac)
                r["load1_before"] = l0
                if r["outlier_flag"]:
                    print("  Ausreisser bei %s ac=%s, wiederhole..." % (item["label"], ac))
                    time.sleep(1.0)
                    r2 = run_point(item, ac)
                    r2["load1_before"] = load1()
                    r2["repeat_of_outlier"] = True
                    out.append(r)  # roh behalten, aber als Ausreisser markiert
                    r = r2
                out.append(r)
                print("  %-12s ac=%-5s med=%6.3fs wer=%.3f load1=%.2f%s"
                      % (item["label"], ac, r["median_s"], r["wer"], r["load1_before"],
                         " OUTLIER-REPEAT" if r.get("repeat_of_outlier") else ""), flush=True)
    finally:
        stop_server(p, log)
        print("Server gestoppt. Prozess lebt noch:", p.poll() is None)
    with open(os.path.join(HERE, "ac_sweep_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
