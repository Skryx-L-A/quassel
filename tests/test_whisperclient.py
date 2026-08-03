"""Tests des curl-Argumentbaus für /inference (Sprache auto/mixed/fest, Prompt,
audio_ctx-Feld für kurze Diktate)."""
import contextlib
import sys, os
import wave
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import whisperclient as wc


def _make_wav(path, duration_s, rate=16000):
    """Stille WAV-Datei fester Dauer (16-bit mono) für die audio_ctx-Tests --
    keine echte Aufnahme nötig, nur eine gültige Header+Frames-Struktur."""
    nframes = int(duration_s * rate)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\x00\x00" * nframes)
    return str(path)


class Cfg:
    def __init__(self, language):
        self.language = language


def _joined(args):
    return " ".join(args)


def test_auto_has_no_language_field():
    args = wc.build_inference_args("a.wav", Cfg("auto"), [])
    assert "language=auto" not in _joined(args)
    assert not any(a.startswith("language=") for a in args)
    assert "prompt=" not in _joined(args)              # keine Wörter -> kein Prompt


def test_fixed_language_sets_field():
    args = wc.build_inference_args("a.wav", Cfg("de"), [])
    assert "language=de" in args


def test_mixed_adds_primer_and_no_hard_language():
    args = wc.build_inference_args("a.wav", Cfg("mixed"), [])
    assert not any(a.startswith("language=") for a in args)   # auto-Erkennung
    prompt = next(a for a in args if a.startswith("prompt="))
    assert wc.MIXED_PRIMER in prompt


def test_dictionary_words_join_into_prompt():
    args = wc.build_inference_args("a.wav", Cfg("auto"), ["PyTorch", "NASA"])
    prompt = next(a for a in args if a.startswith("prompt="))
    assert "PyTorch" in prompt and "NASA" in prompt


def test_mixed_combines_primer_and_words():
    args = wc.build_inference_args("a.wav", Cfg("mixed"), ["Kubernetes"])
    prompt = next(a for a in args if a.startswith("prompt="))
    assert wc.MIXED_PRIMER in prompt and "Kubernetes" in prompt


# ------------------------------------------------------------- audio_ctx
def test_short_wav_gets_audio_ctx_field(tmp_path):
    wavpath = _make_wav(tmp_path / "short.wav", 3.0)
    args = wc.build_inference_args(wavpath, Cfg("auto"), [])
    assert f"audio_ctx={wc.AUDIO_CTX_SHORT}" in args


def test_long_wav_has_no_audio_ctx_field(tmp_path):
    wavpath = _make_wav(tmp_path / "long.wav", 15.0)
    args = wc.build_inference_args(wavpath, Cfg("auto"), [])
    assert not any(a.startswith("audio_ctx=") for a in args)


def test_wav_at_exactly_the_threshold_has_no_audio_ctx_field(tmp_path):
    """Definiertes Verhalten an der Grenze: die Bedingung ist "< 10.0s",
    exakt 10.0s zählt schon als nicht mehr kurz genug (Sicherheitsabstand
    zum letzten sauber gemessenen Punkt bleibt so auf der sicheren Seite)."""
    wavpath = _make_wav(tmp_path / "boundary.wav", wc.AUDIO_CTX_MAX_SECONDS)
    args = wc.build_inference_args(wavpath, Cfg("auto"), [])
    assert not any(a.startswith("audio_ctx=") for a in args)


def test_just_under_the_threshold_gets_the_field(tmp_path):
    wavpath = _make_wav(tmp_path / "just_under.wav", wc.AUDIO_CTX_MAX_SECONDS - 0.1)
    args = wc.build_inference_args(wavpath, Cfg("auto"), [])
    assert f"audio_ctx={wc.AUDIO_CTX_SHORT}" in args


def test_missing_wav_omits_the_field_without_raising():
    args = wc.build_inference_args("/nope/does-not-exist.wav", Cfg("auto"), [])
    assert not any(a.startswith("audio_ctx=") for a in args)


def test_corrupt_wav_omits_the_field_without_raising(tmp_path):
    wavpath = tmp_path / "garbage.wav"
    wavpath.write_bytes(b"not actually a wav file")
    args = wc.build_inference_args(str(wavpath), Cfg("auto"), [])
    assert not any(a.startswith("audio_ctx=") for a in args)


def test_wav_duration_s_returns_none_for_missing_file():
    assert wc.wav_duration_s("/nope/does-not-exist.wav") is None


def test_wav_duration_s_reads_real_duration(tmp_path):
    wavpath = _make_wav(tmp_path / "five.wav", 5.0)
    assert wc.wav_duration_s(wavpath) == 5.0


def test_threshold_stays_within_the_measured_range():
    """Die Schwelle darf den letzten Messpunkt mit unveränderter
    Wortfehlerrate (12,62s) nicht überschreiten: jede Aufnahme, die
    audio_ctx bekommt, soll höchstens so lang sein wie eine, die sauber
    gemessen wurde. Bei 14,34s war die Wortfehlerrate bereits doppelt so
    hoch, ab 18,03s kippt die Dekodierung ganz. Wer die Schwelle anhebt,
    braucht dafür eine neue Messreihe -- nicht nur ein grünes Gefühl."""
    assert wc.AUDIO_CTX_MAX_SECONDS <= 12.62
    assert wc.AUDIO_CTX_SHORT == 768


def test_ensure_server_returns_at_once_when_up(monkeypatch):
    started = []
    monkeypatch.setattr(wc, "server_up", lambda timeout=2: True)
    monkeypatch.setattr(wc, "STARTER", lambda: started.append(1))
    assert wc.ensure_server(deadline=0.1) is True
    assert started == []                       # läuft schon -> nichts starten


class _FakeProbe:
    """Ersatz für wc._probe. open() gibt einen Kontextmanager zurück — genau
    das, was server_up erwartet; ein blankes object() würde durchrutschen und
    die Probe stillschweigend an den echten Server auf 8765 schicken."""

    def __init__(self, calls=None, exc=None):
        self.calls, self.exc = calls if calls is not None else [], exc

    def open(self, url, timeout=None):
        self.calls.append((url, timeout))
        if self.exc:
            raise self.exc
        return contextlib.nullcontext()


def test_server_up_true_when_probe_succeeds(monkeypatch):
    probe = _FakeProbe()
    monkeypatch.setattr(wc, "_probe", probe)
    assert wc.server_up(timeout=3) is True
    assert probe.calls == [(wc.SERVER + "/", 3)]
    assert wc.server_was_up() is True


def test_server_up_false_on_any_exception(monkeypatch):
    monkeypatch.setattr(wc, "_probe", _FakeProbe(exc=OSError("connection refused")))
    wc._server_seen = False
    assert wc.server_up() is False
    assert wc.server_was_up() is False


def test_server_up_does_not_shell_out_to_curl(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("server_up hat curl aufgerufen statt urllib")
    monkeypatch.setattr(wc.subprocess, "run", boom)
    monkeypatch.setattr(wc, "_probe", _FakeProbe())
    assert wc.server_up() is True


def test_server_up_probe_ignores_system_proxies(monkeypatch):
    """Der Server läuft auf 127.0.0.1: ein Systemproxy darf die Probe nicht
    umleiten, sonst gilt ein laufender Server als tot und wird neu gestartet.
    Ein ProxyHandler({}) registriert keine *_open-Methoden und wird von
    build_opener deshalb gar nicht eingehängt — genau das ist die Garantie."""
    import urllib.request
    assert not any(isinstance(h, urllib.request.ProxyHandler)
                   for h in wc._probe.handlers)
    # Gegenprobe, damit der Test nicht leer läuft: der Standard-Opener, den
    # urlopen benutzt, würde unter einem Proxy sehr wohl einen einhängen.
    monkeypatch.setenv("http_proxy", "http://192.0.2.1:9")
    assert any(isinstance(h, urllib.request.ProxyHandler)
               for h in urllib.request.build_opener().handlers)


def test_ensure_server_gives_up_after_the_deadline(monkeypatch):
    """Die Frist ist eine Wanduhr-Frist: der Aufrufer am Diktat-Ende darf
    nicht zwei Minuten warten, nur weil der Server nicht hochkommt."""
    import time
    monkeypatch.setattr(wc, "server_up", lambda timeout=2: False)
    monkeypatch.setattr(wc, "STARTER", lambda: None)
    t0 = time.monotonic()
    assert wc.ensure_server(deadline=0.2) is False
    assert time.monotonic() - t0 < 5


if __name__ == "__main__":
    for fn in [test_auto_has_no_language_field, test_fixed_language_sets_field,
               test_mixed_adds_primer_and_no_hard_language,
               test_dictionary_words_join_into_prompt, test_mixed_combines_primer_and_words]:
        fn(); print("ok:", fn.__name__)
    print("ALL WHISPERCLIENT TESTS PASSED")
