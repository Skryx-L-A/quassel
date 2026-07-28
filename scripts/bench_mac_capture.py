#!/usr/bin/env python3
"""Vergleicht die macOS-Aufnahmepfade von Quassel am SELBEN Mikrofon.

Verglichen werden die drei Backends aus quassel.audio (QUASSEL_MAC_AUDIO):

    ffmpeg      Subprozess, ffmpeg/AVFoundation resampelt per swresample
    sd16        sounddevice-Stream direkt auf 16 kHz (CoreAudio konvertiert)
    sdnative    sounddevice-Stream auf der Geraeterate + eigenes Resampling

Aufbau: CoreAudio erlaubt mehrere Clients am selben Geraet, deshalb nehmen alle
drei Backends GLEICHZEITIG auf — der akustische Input ist damit identisch und
der Vergleich fair. Jedes Backend laeuft als eigener Prozess (ein Prozess kann
dasselbe Geraet nicht zweimal mit verschiedenen Raten oeffnen) und benutzt die
echte quassel.audio.Recorder-Klasse, nicht eine Nachbildung.

Sprachquelle: lokal erzeugte Kokoro-Saetze (~/.local/bin/tts), einmal ueber die
eingebauten Lautsprecher abgespielt. Transkribiert wird ueber den vorhandenen
whisper-Pfad des Projekts (quassel.whisperclient gegen den laufenden
whisper-server).

    .venv/bin/python scripts/bench_mac_capture.py
    .venv/bin/python scripts/bench_mac_capture.py --sentences 3 --json out.json
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from quassel.audio import RATE, SAMPLE_BYTES, Recorder, wav_from_raw  # noqa: E402

BACKENDS = ("ffmpeg", "sd16", "sdnative")

# Phonetisch breite, kurze Saetze (Harvard-Stil) — bekannte Referenz fuer WER.
SENTENCES = [
    "The birch canoe slid on the smooth planks.",
    "Glue the sheet to the dark blue background.",
    "It is easy to tell the depth of a well.",
    "The juice of lemons makes fine punch.",
    "Four hours of steady work faced us.",
    "Large size in stockings is hard to sell.",
    "The boy was there when the sun rose.",
    "A rod is used to catch pink salmon.",
    "The source of the huge river is the clear spring.",
    "Kick the ball straight and follow through.",
]

# Vorlauf grosszuegig: ffmpeg verliert beim Oeffnen von AVFoundation ~0,4 s,
# das Rauschboden-Fenster muss trotzdem in ALLEN Aufnahmen noch Stille sein.
LEAD_SILENCE = 1.5      # s Ruhe vor dem Satz
NOISE_WINDOW = 0.5      # s davon werden als Rauschboden ausgewertet
TAIL_SILENCE = 0.5      # s Ruhe nach dem Satz
FRAME_MS = 20
CLIP_LEVEL = 32700
PLAY_PEAK = 0.45        # Aussteuerung der Beschallung (gemessen: kein Clipping)


# --------------------------------------------------------------- Worker-Modus
def run_capture(backend, raw_path, mic):
    """Ein Aufnahme-Prozess: startet den echten Recorder, meldet READY, stoppt
    auf ein Zeichen von stdin und gibt seine Messwerte als JSON aus."""
    os.environ["QUASSEL_MAC_AUDIO"] = backend
    rec = Recorder(raw_path=raw_path)
    t_call = time.time()
    if not rec.start(mic):
        print(json.dumps({"error": f"{backend}: Recorder.start() fehlgeschlagen"}))
        return 1
    t_open = time.time()
    print("READY", flush=True)
    sys.stdin.readline()
    t_stop = time.time()
    # Referenz VOR dem Stoppen sichern: Recorder.stop() setzt .mac auf None,
    # danach waeren Overflow-Zaehler und Geraeterate nicht mehr auslesbar.
    mac = getattr(rec, "mac", None)
    print(f"stop angefordert bei {t_stop:.3f}", file=sys.stderr, flush=True)
    rec.stop()
    print(f"stop fertig nach {(time.time()-t_stop)*1000:.0f} ms",
          file=sys.stderr, flush=True)
    data = rec.raw_bytes()
    print(json.dumps({
        "backend": backend,
        "bytes": len(data),
        "seconds": len(data) / (RATE * SAMPLE_BYTES),
        "open_ms": (t_open - t_call) * 1000.0,
        "stop_ms": (time.time() - t_stop) * 1000.0,
        "window_s": t_stop - t_open,
        "overflows": getattr(mac, "overflows", None),
        "in_rate": getattr(mac, "in_rate", None),
        "first_block_ms": (None if mac is None or mac.first_block is None
                           else (mac.first_block - mac.started) * 1000.0),
    }), flush=True)
    return 0


# ------------------------------------------------------------------- Messung
def analyse(raw_path, noise_s=NOISE_WINDOW):
    """Pegel/SNR/Clipping aus einer Rohaufnahme (s16le, 16 kHz, mono)."""
    import numpy as np
    with open(raw_path, "rb") as f:
        data = f.read()
    data = data[:len(data) - (len(data) % SAMPLE_BYTES)]
    x = np.frombuffer(data, "<i2").astype(np.float64)
    if x.size < RATE // 2:
        return None
    frame = int(RATE * FRAME_MS / 1000)
    blocks = x[:x.size - x.size % frame].reshape(-1, frame)
    rms = np.sqrt((blocks ** 2).mean(axis=1))
    lead_frames = max(1, int(noise_s * 1000 / FRAME_MS))
    noise = float(np.median(rms[:lead_frames]))
    # Sprache = Frames deutlich ueber dem Rauschboden (mind. ~1 % Vollausschlag)
    speech = rms[lead_frames:][rms[lead_frames:] > max(noise * 3.0, 300.0)]
    speech_rms = float(np.sqrt((speech ** 2).mean())) if speech.size else 0.0
    peak = float(np.abs(x).max())
    return {
        "seconds": x.size / RATE,
        "noise_rms": noise,
        "speech_rms": speech_rms,
        "speech_frames": int(speech.size),
        "snr_db": (20 * math.log10(speech_rms / noise)
                   if speech_rms > 0 and noise > 0 else float("nan")),
        "peak": peak,
        "peak_dbfs": 20 * math.log10(peak / 32768.0) if peak else float("-inf"),
        "clipped": int((np.abs(x) >= CLIP_LEVEL).sum()),
    }


def read_raw(path):
    import numpy as np
    with open(path, "rb") as f:
        data = f.read()
    return np.frombuffer(data[:len(data) - (len(data) % SAMPLE_BYTES)],
                         "<i2").astype(np.float64)


def lag_samples(ref, sig, max_lag=RATE):
    """Versatz von sig gegenueber ref in Samples (positiv: sig faengt spaeter
    an, ihm fehlt also Audio am Anfang). Kreuzkorrelation ueber FFT."""
    import numpy as np
    n = 1 << int(math.ceil(math.log2(max(ref.size, sig.size) + max_lag + 1))) + 1
    a = np.fft.rfft(ref - ref.mean(), n)
    b = np.fft.rfft(sig - sig.mean(), n)
    c = np.fft.irfft(a * np.conj(b), n)
    window = np.concatenate((c[n - max_lag:], c[:max_lag + 1]))
    return int(np.argmax(window)) - max_lag


def common_window(sigs, lags):
    """Gemeinsames Zeitfenster aller Aufnahmen (in ref-Zeit) als Slices."""
    start = max(lags)                                  # spaetester Anfang
    end = min(l + s.size for l, s in zip(lags, sigs))  # fruehestes Ende
    return [(start - l, end - l) for l in lags], max(0, end - start)


# ------------------------------------------------------------------ Playback
def load_wav(path):
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: nur 16-bit-WAV unterstuetzt (ist {width*8} bit)")
    import numpy as np
    a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, rate


def tone_integrity(x, f0, played_s):
    """Vollstaendigkeit einer Dauerton-Aufnahme ueber die PERIODENZAHL.

    Ein f0-Ton der Laenge T enthaelt f0*T Perioden. Diese Zahl haengt NICHT
    davon ab, ob der Pfad das Material zeitlich staucht — sie sinkt nur, wenn
    Audio wirklich verworfen wird. Zusammen mit der gemessenen Tonhoehe trennt
    das die beiden Fehlerbilder: fehlende Perioden = Dropouts, verschobene
    Frequenz = Taktfehler. Rueckgabe: (Perioden, erwartet, Segmentlaenge s,
    Peakfrequenz Hz)."""
    import numpy as np
    n = 1 << int(math.ceil(math.log2(max(x.size, 2))))
    spec = np.fft.rfft(x, n)
    freq = np.fft.rfftfreq(n, 1 / RATE)
    spec[(freq < f0 * 0.6) | (freq > f0 * 1.4)] = 0
    y = np.fft.irfft(spec, n)[:x.size]
    fr = int(0.02 * RATE)
    rms = np.sqrt((y[:y.size - y.size % fr].reshape(-1, fr) ** 2).mean(axis=1))
    on = np.where(rms > rms.max() * 0.3)[0]
    if on.size < 5:
        return 0, int(f0 * played_s), 0.0, float("nan")
    seg = y[on[0] * fr:(on[-1] + 1) * fr]
    periods = int(((seg[:-1] < 0) & (seg[1:] >= 0)).sum())
    win = seg * np.hanning(seg.size)
    spec = np.abs(np.fft.rfft(win, 1 << 20))
    freq = np.fft.rfftfreq(1 << 20, 1 / RATE)
    k = int(spec.argmax())
    delta = 0.5 * (spec[k - 1] - spec[k + 1]) / (spec[k - 1] - 2 * spec[k]
                                                 + spec[k + 1])
    return (periods, int(round(f0 * played_s)), seg.size / RATE,
            float(freq[k] + delta * (freq[1] - freq[0])))


def band_rms(sig, lo, hi):
    import numpy as np
    n = 1 << int(math.ceil(math.log2(max(sig.size, 2))))
    spec = np.fft.rfft(sig, n)
    freq = np.fft.rfftfreq(n, 1 / RATE)
    spec[(freq < lo) | (freq > hi)] = 0
    return float(np.sqrt((np.fft.irfft(spec, n)[:sig.size] ** 2).mean()))


def alias_energy(x, f1, f2, sweep_s, guard=0.15):
    """Wieviel Ultraschall spiegelt der Pfad ins Nutzband zurueck?

    Gespielt wird ein Sweep f1 -> f2 der Laenge sweep_s. Ab dem Zeitpunkt, an
    dem er 8 kHz ueberschreitet, liegt er ueber der Nyquistgrenze von 16 kHz
    und MUSS weggefiltert sein; was dann noch im Band ankommt, ist Aliasing.
    Gemessen wird gegen den Pegel des LEGITIMEN Sweep-Teils, damit die Backends
    trotz leicht verschiedener Aussteuerung vergleichbar bleiben.

    Das Zeitfenster kommt aus dem bekannten Sweep-Fahrplan, nicht aus einer
    Pegelschwelle: oberhalb von 8 kHz ist die Aufnahme ja gerade LEISE, eine
    Schwelle wuerde genau den interessanten Teil abschneiden.
    Rueckgabe: (Alias-RMS, Signal-RMS, Abstand in dB)."""
    import numpy as np
    fr = int(0.02 * RATE)
    rms = np.sqrt((x[:x.size - x.size % fr].reshape(-1, fr) ** 2).mean(axis=1))
    floor = float(np.median(rms[:max(1, int(0.3 * RATE / fr))]))
    on = np.where(rms > max(floor * 8, 300))[0]
    if on.size < 3:
        return float("nan"), float("nan"), float("nan")
    t0 = on[0] * fr                              # Sweep-Start im Sample-Index
    cross = (8000.0 - f1) / (f2 - f1) * sweep_s  # wann der Sweep 8 kHz reisst
    lo, hi = 1500.0, 7500.0
    sig = x[t0:t0 + int((cross - guard) * RATE)]
    alias = x[t0 + int((cross + guard) * RATE):t0 + int(sweep_s * RATE)]
    if sig.size < RATE // 10 or alias.size < RATE // 10:
        return float("nan"), float("nan"), float("nan")
    a, s = band_rms(alias, lo, hi), band_rms(sig, lo, hi)
    return a, s, (20 * math.log10(a / s) if a > 0 and s > 0 else float("nan"))


def speaker_index(name):
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_output_channels", 0) > 0 and d["name"] == name:
            return i
    raise SystemExit(f"Ausgabegeraet '{name}' nicht gefunden")


def play(audio, rate, device, peak=PLAY_PEAK):
    import numpy as np
    import sounddevice as sd
    top = float(np.abs(audio).max()) or 1.0
    sd.play((audio / top * peak).astype(np.float32), rate, device=device,
            blocking=True)


# ------------------------------------------------------------------- Ablauf
def start_workers(mic, tag, outdir):
    """Alle Backends gleichzeitig starten; erst zurueck, wenn alle READY sind.

    stderr geht in eine Datei, nicht in eine Pipe: eine ungelesene Pipe laeuft
    voll und blockiert den Worker (PortAudio meldet gelegentlich viel)."""
    procs = {}
    for backend in BACKENDS:
        raw = os.path.join(outdir, f"{tag}-{backend}.raw")
        log = open(raw + ".log", "w+", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--capture", backend,
             raw, mic],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=log, text=True)
        procs[backend] = (p, raw, log)
    for backend, (p, _, log) in procs.items():
        line = p.stdout.readline().strip()
        if line != "READY":
            log.seek(0)
            raise SystemExit(f"{backend} startete nicht: {line} {log.read()}")
    return procs


def stop_workers(procs, timeout=60):
    stats = {}
    for _, (p, _, _) in procs.items():
        p.stdin.write("\n")
        p.stdin.flush()
    for backend, (p, raw, log) in procs.items():
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
            log.seek(0)
            raise SystemExit(f"{backend} haengt beim Stoppen ({timeout}s), "
                             f"abgeschossen. stderr:\n{log.read()}")
        line = [x for x in out.splitlines() if x.startswith("{")]
        log.seek(0)
        err = log.read().strip()
        log.close()
        if not line:
            raise SystemExit(f"{backend} lieferte keine Messwerte: {out} {err}")
        info = json.loads(line[-1])
        info["raw"] = raw
        info["stderr"] = err
        stats[backend] = info
    return stats


def transcribe(wav_path, cfg):
    """Ueber den Whisper-Pfad des Projekts (quassel.whisperclient)."""
    from quassel import whisperclient
    return (whisperclient.transcribe(wav_path, cfg, timeout=120) or "").strip()


def write_wav(samples, path):
    import numpy as np
    wav_from_raw(np.clip(samples, -32768, 32767).astype("<i2").tobytes(), path)


def align_group(infos):
    """Aufnahmen desselben Satzes zeitlich zueinander ausrichten.

    Referenz ist das erste Backend; die Kreuzkorrelation liefert je Aufnahme den
    Startversatz, das gemeinsame Fenster schneidet alle auf denselben
    akustischen Ausschnitt. Ohne das misst die WER auch den Startverzug mit
    (ffmpeg verliert beim Oeffnen von AVFoundation Audio am Anfang) statt nur
    die Signalqualitaet."""
    sigs = [read_raw(i["raw"]) for i in infos]
    lags = [0] + [lag_samples(sigs[0], s) for s in sigs[1:]]
    slices, length = common_window(sigs, lags)
    for info, sig, lag, (a, b) in zip(infos, sigs, lags, slices):
        info["lag_ms"] = lag / RATE * 1000.0
        info["aligned_wav"] = info["raw"].replace(".raw", "-aligned.wav")
        write_wav(sig[a:b], info["aligned_wav"])
    return length / RATE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", nargs=3, metavar=("BACKEND", "RAW", "MIC"),
                    help="interner Worker-Modus")
    ap.add_argument("--mic", default="MacBook Pro-Mikrofon")
    ap.add_argument("--speaker", default="MacBook Pro-Lautsprecher")
    ap.add_argument("--sentences", type=int, default=len(SENTENCES))
    ap.add_argument("--drift-seconds", type=float, default=20.0,
                    help="stille Zusatzaufnahme fuer die Sample-Rate-Drift")
    ap.add_argument("--tone-seconds", type=float, default=8.0,
                    help="Dauerton-Aufnahme fuer die Dropout-Messung")
    ap.add_argument("--tone-hz", type=float, default=440.0)
    ap.add_argument("--sweep-seconds", type=float, default=2.5,
                    help="Sweep 6-11,5 kHz fuer die Aliasing-Messung")
    ap.add_argument("--outdir", default="/tmp/quassel-bench")
    ap.add_argument("--json", default="")
    ap.add_argument("--keep-dictionary", action="store_true",
                    help="Nutzer-Woerterbuch als Whisper-Prompt mitschicken "
                         "(Standard: aus — die deutschen Eintraege biasen die "
                         "englischen Testsaetze und verrauschen die WER)")
    args = ap.parse_args()

    if args.capture:
        return run_capture(*args.capture)

    os.makedirs(args.outdir, exist_ok=True)
    from quassel import config
    cfg = config.Cfg()
    cfg.language = "en"
    if not args.keep_dictionary:
        config.dictionary_words = lambda: []

    # 1. Saetze lokal erzeugen (Kokoro; wird zwischen Laeufen wiederverwendet)
    texts = SENTENCES[:args.sentences]
    wavs = []
    for i, text in enumerate(texts):
        prefix = os.path.join(args.outdir, f"tts{i:02d}")
        path = prefix + "_000.wav"
        if not os.path.exists(path):
            r = subprocess.run([os.path.expanduser("~/.local/bin/tts"),
                                "-o", prefix, text],
                               capture_output=True, text=True, check=False)
            if r.returncode != 0 or not os.path.exists(path):
                raise SystemExit(f"tts fehlgeschlagen: {r.stderr.strip()}")
        wavs.append(path)
        print(f"tts {i+1}/{len(texts)}: {path}", file=sys.stderr)

    spk = speaker_index(args.speaker)
    results = {b: [] for b in BACKENDS}
    drift = {}

    # 2. Stille Drift-Messung (ohne Beschallung)
    if args.drift_seconds > 0:
        print(f"drift: {args.drift_seconds:.0f} s stille Aufnahme", file=sys.stderr)
        procs = start_workers(args.mic, "drift", args.outdir)
        time.sleep(args.drift_seconds)
        drift = stop_workers(procs)

    # 2b. Dauerton: Dropouts (Phasenspruenge) von Taktdrift (Rampe) trennen
    tone = {}
    if args.tone_seconds > 0:
        import numpy as np
        print(f"ton: {args.tone_seconds:.0f} s {args.tone_hz:.0f} Hz",
              file=sys.stderr)
        t = np.arange(int(args.tone_seconds * 24000)) / 24000.0
        sig = np.sin(2 * np.pi * args.tone_hz * t).astype(np.float32)
        procs = start_workers(args.mic, "tone", args.outdir)
        time.sleep(0.5)
        play(sig, 24000, spk, peak=0.3)
        time.sleep(0.3)
        tone = stop_workers(procs)
        for backend, info in tone.items():
            got, want, secs, peak = tone_integrity(
                read_raw(info["raw"]), args.tone_hz, args.tone_seconds)
            info.update(periods=got, periods_expected=want, tone_s=secs,
                        peak_hz=peak,
                        pitch_ppm=(peak / args.tone_hz - 1) * 1e6)

    # 2c. Sweep 6 -> 11,5 kHz: prueft die Anti-Aliasing-Filter der Resampler
    sweep = {}
    if args.sweep_seconds > 0:
        import numpy as np
        print(f"sweep: {args.sweep_seconds:.1f} s 6-11,5 kHz", file=sys.stderr)
        f1, f2 = 6000.0, 11500.0
        t = np.arange(int(args.sweep_seconds * 48000)) / 48000.0
        phase = 2 * np.pi * (f1 * t + (f2 - f1) / (2 * args.sweep_seconds) * t * t)
        sig = np.sin(phase).astype(np.float32)
        procs = start_workers(args.mic, "sweep", args.outdir)
        time.sleep(0.5)
        play(sig, 48000, spk, peak=0.25)
        time.sleep(0.3)
        sweep = stop_workers(procs)
        for backend, info in sweep.items():
            a, sg, db = alias_energy(read_raw(info["raw"]), f1, f2,
                                     args.sweep_seconds)
            info.update(alias_rms=a, sweep_rms=sg, alias_db=db)

    # 3. Pro Satz: alle Backends gleichzeitig, einmal beschallen
    for i, (text, wav) in enumerate(zip(texts, wavs)):
        audio, rate = load_wav(wav)
        print(f"satz {i+1}/{len(texts)}: {len(audio)/rate:.1f}s — {text}",
              file=sys.stderr)
        procs = start_workers(args.mic, f"s{i:02d}", args.outdir)
        time.sleep(LEAD_SILENCE)
        play(audio, rate, spk)
        time.sleep(TAIL_SILENCE)
        stats = stop_workers(procs)
        for backend, info in stats.items():
            info["text"] = text
            info["metrics"] = analyse(info["raw"])
            results[backend].append(info)

    # 4. Ausrichten, transkribieren, WER
    from benchmark_stt_mac import word_error_rate
    for i in range(len(texts)):
        group = [results[b][i] for b in BACKENDS]
        secs = align_group(group)
        print(f"align satz {i+1}: gemeinsames Fenster {secs:.2f}s  "
              + "  ".join(f"{b}={r['lag_ms']:+.0f}ms"
                          for b, r in zip(BACKENDS, group)), file=sys.stderr)
    for backend in BACKENDS:
        for info in results[backend]:
            raw_wav = info["raw"].replace(".raw", ".wav")
            with open(info["raw"], "rb") as f:
                data = f.read()
            wav_from_raw(data[:len(data) - (len(data) % SAMPLE_BYTES)], raw_wav)
            info["hyp"] = transcribe(raw_wav, cfg)
            info["wer"] = word_error_rate(info["text"], info["hyp"])
            info["hyp_aligned"] = transcribe(info["aligned_wav"], cfg)
            info["wer_aligned"] = word_error_rate(info["text"], info["hyp_aligned"])
            print(f"  {backend:9s} wer={info['wer']:.3f} "
                  f"aligned={info['wer_aligned']:.3f}  {info['hyp'][:60]}",
                  file=sys.stderr)

    report(results, drift, tone, sweep, args)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"sentences": results, "drift": drift, "tone": tone,
                       "sweep": sweep}, f, indent=2)
    return 0


def report(results, drift, tone, sweep, args):
    def avg(vals):
        vals = [v for v in vals if v is not None and not math.isnan(v)]
        return sum(vals) / len(vals) if vals else float("nan")

    print()
    print(f"Mikrofon: {args.mic}   Lautsprecher: {args.speaker}   "
          f"Saetze: {args.sentences}")
    print()
    head = ("backend", "WER", "WERalgn", "SNR dB", "speech", "noise",
            "peak dBFS", "clip", "start ms", "verlust s", "open ms",
            "overflow", "drift ppm")
    print("{:9s} {:>6s} {:>8s} {:>7s} {:>7s} {:>6s} {:>10s} {:>5s} {:>9s} "
          "{:>10s} {:>8s} {:>9s} {:>10s}".format(*head))
    for backend in BACKENDS:
        rows = results[backend]
        met = [r["metrics"] for r in rows if r["metrics"]]
        lost = [r["window_s"] - r["seconds"] for r in rows]
        # Drift: der konstante Start-/Stopp-Versatz faellt heraus, wenn man den
        # Verlust einer langen mit dem einer kurzen Aufnahme vergleicht.
        d = drift.get(backend)
        ppm = float("nan")
        if d:
            dt = d["window_s"] - avg([r["window_s"] for r in rows])
            if dt > 1:
                ppm = (avg(lost) - (d["window_s"] - d["seconds"])) / dt * 1e6
        print("{:9s} {:6.3f} {:8.3f} {:7.1f} {:7.0f} {:6.1f} {:10.1f} {:5d} "
              "{:9.0f} {:10.3f} {:8.1f} {:9s} {:10.0f}".format(
                  backend,
                  avg([r["wer"] for r in rows]),
                  avg([r.get("wer_aligned") for r in rows]),
                  avg([m["snr_db"] for m in met]),
                  avg([m["speech_rms"] for m in met]),
                  avg([m["noise_rms"] for m in met]),
                  avg([m["peak_dbfs"] for m in met]),
                  sum(m["clipped"] for m in met),
                  avg([r.get("lag_ms") for r in rows]),
                  avg(lost),
                  avg([r["open_ms"] for r in rows]),
                  str(sum(r["overflows"] or 0 for r in rows)),
                  ppm))
    if tone:
        print()
        want = tone[BACKENDS[0]]["periods_expected"]
        print(f"Dauerton {args.tone_hz:.0f} Hz, {args.tone_seconds:.0f} s = "
              f"{want} Perioden — fehlende Perioden = verworfenes Audio, "
              "verschobene Tonhoehe = Taktfehler")
        print("{:9s} {:>10s} {:>10s} {:>10s} {:>10s} {:>10s}".format(
            "backend", "datei s", "ton s", "perioden", "vollst. %", "ton ppm"))
        for backend in BACKENDS:
            t = tone.get(backend)
            if not t:
                continue
            print("{:9s} {:10.3f} {:10.3f} {:10d} {:10.1f} {:10.0f}".format(
                backend, t["seconds"], t["tone_s"], t["periods"],
                t["periods"] / want * 100.0, t["pitch_ppm"]))
    if sweep:
        print()
        print("Sweep 6-11,5 kHz — Energie im 1,5-7,5 kHz-Band waehrend des "
              "Sweep-Teils UEBER 8 kHz,\ngemessen gegen den legitimen Teil "
              "darunter (negativer = besser gefiltert)")
        print("{:9s} {:>10s} {:>11s} {:>12s}".format(
            "backend", "alias rms", "signal rms", "alias dB"))
        for backend in BACKENDS:
            w = sweep.get(backend)
            if not w:
                continue
            print("{:9s} {:10.1f} {:11.1f} {:12.1f}".format(
                backend, w["alias_rms"], w["sweep_rms"], w["alias_db"]))
    print()
    for backend in BACKENDS:
        rows = results[backend]
        print(f"{backend:9s} WER je Satz: "
              + " ".join(f"{r['wer']:.3f}" for r in rows)
              + "   aligned: "
              + " ".join(f"{r['wer_aligned']:.3f}" for r in rows))


if __name__ == "__main__":
    sys.exit(main())
