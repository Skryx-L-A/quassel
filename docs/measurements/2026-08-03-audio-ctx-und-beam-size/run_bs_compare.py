#!/usr/bin/env python3
"""AUFTRAG 2: -bs 1 gegen -bs 5, auf moeglichst breiter Stichprobe.
Deterministisch bei temperature=0.0 -> ein Lauf je (Datei,Konfiguration)
fuer WER, aber ein zweiter Lauf auf einer Stichprobe zur Determinismus-Probe.
Zeit wird nebenbei mitgenommen (3 Laeufe, Median), ist hier aber nicht die
Hauptfrage. Server einmal mit Produktions-VAD gestartet, beam_size nur als
Anfragefeld variiert (Produktionsdefault bs=5 bleibt Server-Flag)."""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import infer, load1, port_free, start_server, stop_server, word_error_rate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "<repo>"
TIME_RUNS = 3


def load_items():
    items = []
    for d in ("bench_samples", "bench_samples_synth"):
        dd = os.path.join(REPO, d)
        for fn in sorted(os.listdir(dd)):
            if not fn.endswith(".wav"):
                continue
            wav = os.path.join(dd, fn)
            txt = os.path.join(dd, fn[:-4] + ".txt")
            ref = open(txt, encoding="utf-8").read().strip()
            items.append({"label": "%s/%s" % (d, fn), "path": wav, "ref": ref,
                          "kind": "real", "note": "echte Aufnahme aus %s" % d})
    segs = json.load(open(os.path.join(HERE, "segments_manifest.json"), encoding="utf-8"))
    for s in segs:
        items.append({"label": "seg_%02dwords_%.1fs" % (s["word_idx"], s["duration_s"]),
                      "path": s["path"], "ref": s["ref"], "kind": "real",
                      "note": s["source"]})
    synth = json.load(open(os.path.join(HERE, "synth_manifest.json"), encoding="utf-8"))
    for s in synth:
        kind = "synthetic-noisy" if "synth_noisy" in s["path"] else "synthetic-quiet"
        items.append({"label": os.path.basename(s["path"]), "path": s["path"], "ref": s["ref"],
                      "kind": kind, "note": s["source"]})
    return items


def main():
    if not port_free():
        print("Port belegt, Abbruch"); return 1
    items = load_items()
    print("Anzahl Testdateien:", len(items))
    out = []
    p, log = start_server()
    print("Server oben, load1=%.2f" % load1())
    try:
        infer(items[0]["path"])
        for item in items:
            row = {"label": item["label"], "kind": item["kind"], "note": item["note"],
                   "ref_words": len(item["ref"].split())}
            for bs in (1, 5):
                times = []
                hyp = None
                for i in range(TIME_RUNS):
                    el, h, rc = infer(item["path"], beam_size=bs)
                    times.append(el)
                    if hyp is None:
                        hyp = h
                    elif h != hyp:
                        row.setdefault("nondeterminism", []).append({"bs": bs, "run": i, "hyp": h})
                row["bs%d_median_s" % bs] = statistics.median(times)
                row["bs%d_hyp" % bs] = hyp
                row["bs%d_wer" % bs] = word_error_rate(item["ref"], hyp)
            row["wer_delta_bs1_minus_bs5"] = row["bs1_wer"] - row["bs5_wer"]
            row["load1"] = load1()
            out.append(row)
            print("  %-40s bs1 wer=%.3f  bs5 wer=%.3f  delta=%+.3f  [%s] load1=%.2f"
                  % (item["label"], row["bs1_wer"], row["bs5_wer"],
                     row["wer_delta_bs1_minus_bs5"], item["kind"], row["load1"]), flush=True)
    finally:
        stop_server(p, log)
        print("Server gestoppt. Prozess lebt noch:", p.poll() is None)
    with open(os.path.join(HERE, "bs_compare_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
