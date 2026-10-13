#!/usr/bin/env python3
"""Messrahmen: wiederholbare STT-Messungen über SpeechBackend-Instanzen.

Liest ein Manifest (scripts/bench/manifest.py), schickt jede Aufnahme durch
ein SpeechBackend aus quassel.backends und misst pro Clip: normalisiertes WER
(wie die Open-ASR-Leaderboards), sichtbare Zeichensetzungs- und
Großschreibungsfehler, Wörterbuch-Trefferquote, Halluzinationen auf
Stille-Clips sowie Server-Antwortzeit und Gesamtzeit (Median und p95).
Ausgabe als Markdown-Tabelle plus JSON im Stil von docs/measurements/, mit
Modell, Parametern (prompt, audio_ctx, greedy), Plattform und Datum.

Schutzregeln dieses Skripts:
  - Der Server auf 127.0.0.1:8765 gehört der installierten App. Nur lesend
    anfragen: GET / (health) und POST /inference. /load wird nie berührt.
  - warmup()/ensure_server() werden hier nie aufgerufen — das würde den
    Server der installierten App starten oder entladen.
  - Ohne erreichbaren Server läuft kein echter Lauf: sauber übersprungen.

Aufruf:
  python scripts/bench/run_bench.py [MANIFEST] [--runs N] [--json PFAD]
      [--md PFAD] [--root PFAD] [--backend whisper|transcribe-cpp|all]
      [--transcribe-cpp BINÄRPFAD] [--tcpp-model GGUF] [--tcpp-family NAME]
      [--tcpp-compute auto|metal|cuda|vulkan|cpu]

transcribe-cpp startet je Clip einen transcribe-cli-Prozess; die Gesamtzeit
enthält deshalb das Laden des Modells (scripts/tcpp/README.md).

Der Report eines echten Laufs gehört nicht nach docs/measurements/ (das
entscheidet der Orchestrator); --json/--md schreiben dorthin, wo der Aufrufer
es verlangt.
"""
import argparse
import contextlib
import datetime
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _path in (str(_HERE.parent), str(_REPO)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from bench import manifest as manifest_mod          # noqa: E402
from bench import metrics                           # noqa: E402
from quassel.backends import (SpeechResult,         # noqa: E402,F401 (Vertrag)
                              TranscriptionOptions,
                              TranscribeCppBackend,
                              WhisperServerBackend)
from quassel.backends import tcpp_families          # noqa: E402

DEFAULT_TIMEOUT = 120
HYPOTHESIS_PREVIEW_CHARS = 60


def repo_commit(repo=_REPO):
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=False)
    except OSError:
        return None
    return proc.stdout.strip() or None


def _estimated_tokens(text):
    from quassel import dictprompt
    return dictprompt.estimated_tokens(text)


def whisper_request_preview(wav_path, language, timeout):
    """Felder des tatsächlichen /inference-Requests, ohne ihn zu senden."""
    from quassel import config, whisperclient
    words = config.dictionary_words()
    fields = whisperclient.inference_fields(
        str(wav_path), SimpleNamespace(language=language), words, timeout)
    preview = {"response_format": None, "temperature": None,
               "audio_ctx": None, "language": None, "prompt": None,
               "greedy": "kein beam_size-Feld gesendet; wirksam ist die "
                         "Einstellung beim Serverstart (/load ist TABU, "
                         "nicht abfragbar)"}
    for name, value in fields:
        if name == "prompt":
            preview["prompt"] = {
                "sent": True,
                "dictionary_words": len(words),
                "tokens_estimate": _estimated_tokens(value),
            }
        elif name in ("response_format", "temperature", "audio_ctx", "language"):
            preview[name] = value
    return preview


def whisper_backend_meta():
    """Modell und Parameter des laufenden whisper-Servers, nur lesend gelesen."""
    from quassel import config, whisperclient
    env = config.read_serverenv()
    model_path = env.get("MODEL_PATH", "")
    model = (os.path.basename(model_path) if model_path else
             "unbekannt (server.env ohne MODEL_PATH; nur lesend gelesen)")
    words = config.dictionary_words()
    params = {
        "server": whisperclient.SERVER,
        "prompt": (f"Wörterbuch mit {len(words)} Einträgen übergeben "
                   "(dictprompt.build_prompt, Tokenbudget 160)"
                   if words else "leer (kein Wörterbuch)"),
        "audio_ctx": (f"768 bei Clips < {whisperclient.AUDIO_CTX_MAX_SECONDS} s, "
                      "sonst volles Fenster (whisperclient.inference_fields)"),
        "greedy": "Anfrage sendet kein beam_size-Feld; wirksam ist die "
                  "Einstellung beim Serverstart (/load ist TABU)",
        "temperature": 0.0,
        "language": "je Clip aus dem Manifest (de/en/mixed; mixed bekommt "
                    "den Mixed-Primer)",
    }
    return model, params


@contextlib.contextmanager
def _record_server_time(recorder):
    """Dauer der POST /inference-Runde messen, ohne das Produkt zu ändern.

    Nur im Bench-Prozess aktiv; danach wird der Original-Zustand wiederher-
    gestellt. Gezählt wird nur die erfolgreiche Runde — scheitert der direkte
    Post und der curl-Rückfall übernimmt, bleibt die Serverzeit für diesen
    Lauf None statt eine fehlgeschlagene Runde zu melden.
    """
    import quassel.whisperclient as wc
    original = wc._post_multipart

    def timed(*args, **kwargs):
        # Alle Argumente durchreichen: die Signatur von _post_multipart wächst
        # (errors kam dazu), und ein TypeError hier sah aus wie 0,000 s.
        start = time.perf_counter()
        try:
            result = original(*args, **kwargs)
        finally:
            duration = time.perf_counter() - start
        recorder.append((duration, result is not None))
        return result

    wc._post_multipart = timed
    try:
        yield
    finally:
        wc._post_multipart = original


def run_clip(backend, item, wav_path, *, language, runs, timeout,
             whisper_server=False):
    """Einen Clip messen: Ergebnispunkte im Stil von docs/measurements/."""
    duration = manifest_mod.wav_duration_seconds(wav_path)
    ref = item.ref or ""
    totals, servers, texts, errors, notes = [], [], [], [], []
    for _ in range(runs):
        opts = TranscriptionOptions(cfg=SimpleNamespace(language=language),
                                    timeout=timeout, language_hint=language)
        text, server_s, error = None, None, None
        start = time.perf_counter()
        try:
            if whisper_server:
                recorder = []
                with _record_server_time(recorder):
                    result = backend.transcribe(str(wav_path), opts)
                good = [(d, ok) for d, ok in recorder if ok]
                if good:
                    server_s = good[-1][0]
            else:
                result = backend.transcribe(str(wav_path), opts)
            text = result.text
            if getattr(result, "error", None):
                error = result.error
            for note in getattr(result, "notes", ()) or ():
                if note not in notes:
                    notes.append(note)
        except Exception as exc:   # noqa: BLE001 — ein fehlgeschlagener Lauf ist ein Messwert
            error = f"{type(exc).__name__}: {exc}"
        totals.append(time.perf_counter() - start)
        servers.append(server_s)
        texts.append(text)
        if error:
            errors.append(error)
    hyp = texts[0] or ""
    server_times = [s for s in servers if s is not None]
    point = {
        "label": item.id,
        "file": item.path,
        "language": language,
        "category": item.category,
        "duration_s": duration,
        "note": item.source,
        "times_s": totals,
        "median_s": statistics.median(totals) if totals else None,
        "p95_s": metrics.percentile(totals, 95),
        "min_s": min(totals) if totals else None,
        "max_s": max(totals) if totals else None,
        "server_times_s": servers,
        "server_median_s": (statistics.median(server_times)
                            if server_times else None),
        "server_p95_s": metrics.percentile(server_times, 95),
        "hyps": texts,
        "hyp": hyp,
        "hyps_identical": len(set(texts)) == 1,
        "ok": bool(hyp.strip()),
        "wer": metrics.word_error_rate(ref, hyp),
    }
    if ref.strip():
        point["punct_err_rate"], point["cap_err_rate"] = \
            metrics.visible_error_rates(ref, hyp)
    else:
        point["punct_err_rate"] = point["cap_err_rate"] = None
    if item.expect_words:
        hits, total = metrics.dictionary_hit_rate(item.expect_words, hyp)
        point["dict_hits"], point["dict_total"] = hits, total
        point["dict_rate"] = (hits / total) if total else None
    else:
        point["dict_hits"] = point["dict_total"] = point["dict_rate"] = None
    if (item.category or "") == "stille":
        from quassel import dictprompt
        point["hallucinated"] = bool(hyp.strip())
        point["known_artifact"] = dictprompt.is_silence_hallucination(hyp)
    if whisper_server and Path(wav_path).exists():
        point["request"] = whisper_request_preview(wav_path, language, timeout)
    if errors:
        point["errors"] = errors
    if notes:
        point["notes"] = notes
    return point


def _group_stats(rows):
    wer = [r["wer"] for r in rows if r.get("wer") is not None]
    punct = [r["punct_err_rate"] for r in rows
             if r.get("punct_err_rate") is not None]
    cap = [r["cap_err_rate"] for r in rows
           if r.get("cap_err_rate") is not None]
    dict_rates = [r["dict_rate"] for r in rows
                  if r.get("dict_rate") is not None]
    halluc = [r["hallucinated"] for r in rows
              if r.get("hallucinated") is not None]
    totals = [t for r in rows for t in r["times_s"]]
    servers = [s for r in rows for s in (r["server_times_s"] or [])
               if s is not None]
    return {
        "clips": len(rows),
        "wer_mean": metrics.mean_or_none(wer),
        "wer_median": metrics.median_or_none(wer),
        "punct_err_rate_mean": metrics.mean_or_none(punct),
        "cap_err_rate_mean": metrics.mean_or_none(cap),
        "dict_rate_mean": metrics.mean_or_none(dict_rates),
        "hallucinated_clips": sum(1 for h in halluc if h),
        "hallucination_rate": (sum(1 for h in halluc if h) / len(halluc)
                               if halluc else None),
        "total_time_s": metrics.summarize_times(totals),
        "server_time_s": metrics.summarize_times(servers),
    }


def summarize_results(results):
    groups = {}
    for r in results:
        groups.setdefault(r.get("language") or "unbestimmt", []).append(r)
    categories = {}
    for r in results:
        categories.setdefault(r.get("category") or "unbestimmt", []).append(r)
    return {
        "overall": _group_stats(results),
        "by_language": {g: _group_stats(rows)
                        for g, rows in sorted(groups.items())},
        "by_category": {g: _group_stats(rows)
                        for g, rows in sorted(categories.items())},
    }


def run_backend_section(name, backend, items, root, *, runs, timeout,
                        model=None, params=None):
    """Alle Clips durch ein Backend schicken; Ergebnisabschnitt des Reports."""
    is_whisper = isinstance(backend, WhisperServerBackend)
    results = []
    for item in items:
        language = item.language or "auto"
        point = run_clip(backend, item, root / item.path, language=language,
                         runs=runs, timeout=timeout, whisper_server=is_whisper)
        results.append(point)
        print(f"  {point['label']}: wer={point['wer']:.3f} "
              f"gesamt={point['median_s']:.3f}s "
              f"server={point['server_median_s']}", file=sys.stderr, flush=True)
    return {
        "name": name,
        "backend": type(backend).__name__,
        "model": model,
        "params": params or {},
        "runs": runs,
        "timeout_s": timeout,
        "results": results,
        "summary": summarize_results(results),
    }


def select_backends(args):
    """(name, backend, whisper_server)-Tripel in Ausführungsreihenfolge."""
    backends = []
    if args.backend in ("whisper", "all"):
        backends.append(("whisper-server", WhisperServerBackend(), True))
    if args.backend in ("transcribe-cpp", "all"):
        if args.transcribe_cpp:
            backends.append(("transcribe-cpp", transcribe_cpp_backend(args),
                             False))
        else:
            print("transcribe-cpp: ohne --transcribe-cpp BINÄRPFAD "
                  "übersprungen", file=sys.stderr)
    return backends


def _dictionary_words():
    from quassel import config
    return config.dictionary_words()


def transcribe_cpp_backend(args):
    """CLI-Adapter wie im Produkt; das Wörterbuch ist dasselbe wie beim
    whisper-Server, wirkt aber nur bei Familien mit vocabulary."""
    return TranscribeCppBackend(
        binary=args.transcribe_cpp,
        model=getattr(args, "tcpp_model", None),
        family=getattr(args, "tcpp_family", None),
        compute=getattr(args, "tcpp_compute", None) or "auto",
        vocabulary=_dictionary_words)


def transcribe_cpp_meta(backend, display_name=None):
    """Modell und Parameter des transcribe-cpp-Abschnitts für den Report."""
    family = backend.family.name if backend.family else None
    quant = backend.quant
    model_file = os.path.basename(backend.model) if backend.model else None
    label = display_name or model_file or "unbekannt (kein --tcpp-model)"
    model = (f"{label} (Familie {family or 'unbekannt'}, "
             f"Quantisierung {quant or 'unbekannt'})")
    if backend.family and backend.family.vocabulary:
        prompt = ("Wörterbuch über --vocabulary-file (dieselbe Liste wie "
                  "beim whisper-Server); kein Kontexttext")
    else:
        prompt = f"nicht unterstützt (Familie {family or 'unbekannt'})"
    params = {
        "binary": backend.binary,
        "model_path": backend.model,
        "model_file": model_file,
        "family": family,
        "quant": quant,
        "compute": backend.compute,
        "upstream": f"transcribe.cpp {tcpp_families.UPSTREAM_TAG}",
        "process": "ein transcribe-cli-Prozess je Clip (Modellladen in der "
                   "Gesamtzeit enthalten)",
        "language": "je Clip aus dem Manifest; Familien ohne Auto-Erkennung "
                    "bekommen bei mixed/auto 'de' (Hinweis im Clip)",
        "prompt": prompt,
        "audio_ctx": "nicht anwendbar (transcribe.cpp)",
        "greedy": "Vorgabe der Modellfamilie (nicht gesteuert)",
    }
    return model, params


def report_for_backends(backends, items, root, *, meta, open_lines, runs,
                        timeout, transcribe_cpp_model=None):
    """Report mit einem Abschnitt je Backend; health()=False → sauber skip."""
    sections = []
    for name, backend, is_whisper in backends:
        if not backend.health():
            print(f"{name}: health()=False — sauber übersprungen "
                  "(kein echter Lauf).", file=sys.stderr)
            sections.append({
                "name": name, "backend": type(backend).__name__,
                "skipped": True, "skipped_reason": "health()=False",
                "model": None, "params": {}, "runs": runs,
                "timeout_s": timeout, "results": [],
                "summary": summarize_results([]),
            })
            continue
        model, params = (None, {})
        if is_whisper:
            model, params = whisper_backend_meta()
        else:
            model, params = transcribe_cpp_meta(backend, transcribe_cpp_model)
        print(f"{name}: gesund, {len(items)} Clips × {runs} Läufe "
              f"(Modell: {model})", file=sys.stderr)
        sections.append(run_backend_section(
            name, backend, items, root, runs=runs, timeout=timeout,
            model=model, params=params))
    return {"meta": meta, "backends": sections, "open": open_lines}


def build_meta(args, manifest_path):
    return {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "platform": f"{platform.system()} {platform.release()} "
                    f"({platform.machine()})",
        "python": platform.python_version(),
        "repo_commit": repo_commit(),
        "manifest": str(Path(manifest_path)),
        "root": str(Path(args.root).expanduser().resolve()),
    }


def _fmt(value, digits=3):
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _hyp_preview(hyp):
    text = " ".join((hyp or "").split()).replace("|", "\\|")
    if len(text) > HYPOTHESIS_PREVIEW_CHARS:
        text = text[:HYPOTHESIS_PREVIEW_CHARS - 1] + "…"
    return text


def render_markdown(report):
    lines = ["# STT-Messrahmen", ""]
    meta = report["meta"]
    lines += [
        f"- Datum: {meta['date']}",
        f"- Plattform: {meta['platform']} (Python {meta['python']})",
        f"- Commit: {meta['repo_commit'] or 'unbekannt'}",
        f"- Manifest: {meta['manifest']}",
        f"- Datenursprung: {meta['root']}",
    ]
    if report["open"]:
        lines += ["", "Offene Punkte:", ""]
        lines += [f"- {o}" for o in report["open"]]
    for section in report["backends"]:
        lines += ["", f"## Backend: {section['name']}", ""]
        if section.get("skipped"):
            lines += [f"Übersprungen: {section['skipped_reason']} "
                      "(kein echter Lauf).", ""]
            continue
        params = section["params"]
        lines += [f"- Modell: {section['model']}"]
        if params.get("family") or params.get("quant"):
            lines += [f"- transcribe.cpp: Familie {params.get('family') or 'n/a'}"
                      f" · Quantisierung {params.get('quant') or 'n/a'}"
                      f" · Rechenwerk {params.get('compute') or 'n/a'}"
                      f" · {params.get('upstream', 'n/a')}"
                      f" · {params.get('process', 'n/a')}"]
        lines += [
            f"- Parameter: prompt: {params.get('prompt', 'n/a')} · "
            f"audio_ctx: {params.get('audio_ctx', 'n/a')} · "
            f"greedy: {params.get('greedy', 'n/a')}",
            f"- Läufe je Clip: {section['runs']} · Timeout: "
            f"{section['timeout_s']} s",
            "",
        ]
        stats = section["summary"]["overall"]
        lines += ["Zusammenfassung:", "",
                  "| Clips | WER Mittel | WER Median | Zeichensetzung | "
                  "Großschreibung | Wörterbuch | Halluzinationen | "
                  "Serverzeit Median s | Gesamtzeit Median s | Gesamtzeit p95 s |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        lines.append(
            f"| {stats['clips']} | {_fmt(stats['wer_mean'])} | "
            f"{_fmt(stats['wer_median'])} | {_fmt(stats['punct_err_rate_mean'])} | "
            f"{_fmt(stats['cap_err_rate_mean'])} | {_fmt(stats['dict_rate_mean'])} | "
            f"{stats['hallucinated_clips']}/{stats['clips']} | "
            f"{_fmt(stats['server_time_s']['median'])} | "
            f"{_fmt(stats['total_time_s']['median'])} | "
            f"{_fmt(stats['total_time_s']['p95'])} |")
        for key, title in (("by_language", "Nach Sprache"),
                           ("by_category", "Nach Kategorie")):
            lines += ["", f"{title}:", "",
                      "| Gruppe | Clips | WER Mittel | Zeichensetzung | "
                      "Großschreibung | Wörterbuch | Halluzinationen | "
                      "Serverzeit Median s | Gesamtzeit Median s |",
                      "|---|---|---|---|---|---|---|---|---|"]
            for group, stats in section["summary"][key].items():
                lines.append(
                    f"| {group} | {stats['clips']} | {_fmt(stats['wer_mean'])} | "
                    f"{_fmt(stats['punct_err_rate_mean'])} | "
                    f"{_fmt(stats['cap_err_rate_mean'])} | "
                    f"{_fmt(stats['dict_rate_mean'])} | "
                    f"{stats['hallucinated_clips']}/{stats['clips']} | "
                    f"{_fmt(stats['server_time_s']['median'])} | "
                    f"{_fmt(stats['total_time_s']['median'])} |")
        lines += ["", "Clips:", "",
                  "| Clip | Sprache | Kategorie | Dauer s | WER | Zeichensetz. | "
                  "Großschr. | Wörterbuch | Server Median s | Gesamt Median s | "
                  "Gesamt p95 s | Hypothese (gekürzt) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in section["results"]:
            dict_cell = (f"{r['dict_hits']}/{r['dict_total']}"
                         if r.get("dict_total") else "n/a")
            lines.append(
                f"| {r['label']} | {r['language']} | {r['category'] or 'n/a'} | "
                f"{_fmt(r['duration_s'], 2)} | {_fmt(r['wer'])} | "
                f"{_fmt(r['punct_err_rate'])} | {_fmt(r['cap_err_rate'])} | "
                f"{dict_cell} | {_fmt(r['server_median_s'])} | "
                f"{_fmt(r['median_s'])} | {_fmt(r['p95_s'])} | "
                f"{_hyp_preview(r['hyp'])} |")
    return "\n".join(lines) + "\n"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Messrahmen für wiederholbare STT-Messungen.")
    parser.add_argument("manifest", nargs="?",
                        default=str(manifest_mod.DEFAULT_MANIFEST),
                        help="Manifest-Datei (JSON oder CSV)")
    parser.add_argument("--runs", type=int, default=1,
                        help="Läufe je Clip (Vorgabe 1)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--root", default=str(_REPO),
                        help="Datenursprung für die Manifest-Pfade")
    parser.add_argument("--json", default=None,
                        help="JSON-Report schreiben (nie unter docs/measurements/)")
    parser.add_argument("--md", default=None, help="Markdown-Report schreiben")
    parser.add_argument("--language", default=None,
                        help="Sprache für Clips ohne Manifest-Sprache "
                             "(sonst auto)")
    parser.add_argument("--backend", choices=("whisper", "transcribe-cpp", "all"),
                        default="whisper")
    parser.add_argument("--transcribe-cpp", default=None, dest="transcribe_cpp",
                        help="Pfad zu transcribe-cli; ohne Angabe wird "
                             "transcribe-cpp übersprungen")
    parser.add_argument("--tcpp-model", default=None, dest="tcpp_model",
                        help="GGUF-Modelldatei für transcribe-cpp")
    parser.add_argument("--tcpp-family", default=None, dest="tcpp_family",
                        choices=sorted(tcpp_families.FAMILIES),
                        help="Modellfamilie (sonst aus dem Dateinamen)")
    parser.add_argument("--tcpp-compute", default="auto", dest="tcpp_compute",
                        choices=("auto", "metal", "cuda", "vulkan", "cpu"),
                        help="Rechenwerk für transcribe-cli --backend")
    parser.add_argument("--transcribe-cpp-model", default=None,
                        dest="transcribe_cpp_model",
                        help="Anzeigename statt des Dateinamens im Report")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    root = Path(args.root).expanduser()
    data = manifest_mod.load_manifest(args.manifest)
    open_lines = list(data.open)
    runnable = []
    for item in data.items:
        if item.ref is None:
            open_lines.append(f"{item.id} ({item.path}): keine Referenz — "
                              "übersprungen")
        elif not (root / item.path).exists():
            open_lines.append(f"{item.id}: Aufnahme fehlt unter "
                              f"{root / item.path} — übersprungen")
        else:
            runnable.append(item)
    if args.language:
        for item in runnable:
            item.language = item.language or args.language
    report = report_for_backends(
        select_backends(args), runnable, root,
        meta=build_meta(args, args.manifest), open_lines=open_lines,
        runs=args.runs, timeout=args.timeout,
        transcribe_cpp_model=args.transcribe_cpp_model)
    markdown = render_markdown(report)
    print(markdown, end="")
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")
    if args.md:
        Path(args.md).write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
