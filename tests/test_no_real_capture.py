"""Gegenprobe zum Aufnahmeschutz in conftest.py: kein Test öffnet ein echtes
Aufnahmegerät, weder über einen Prozess noch über sounddevice.

Nichts hier nimmt wirklich auf. Der Beweis, dass ein Kommando nie lief, ist
eine Markerdatei: ein Platzhalter (der Python-Interpreter, nur unter dem Namen
des Aufnahmewerkzeugs gestartet) legt sie an, sobald er läuft. Die Kontrollprobe
zeigt, dass derselbe Aufbau unter einem harmlosen Namen die Datei wirklich
anlegt — fehlt sie beim Aufnahmenamen, hat der Schutz den Start verhindert.
"""
import importlib
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

import quassel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(quassel.__file__)))

# Mit dem Opt-in ist der Schutz aus; diese Tests setzen ihn voraus.
_OPT_IN = os.environ.get("QUASSEL_LIVE_TESTS") == "1"
pytestmark = pytest.mark.skipif(_OPT_IN, reason="QUASSEL_LIVE_TESTS=1 schaltet den Schutz ab")


def _placeholder(name, marker):
    """Popen-Argumente für einen Prozess, der unter `name` läuft und `marker` anlegt."""
    code = "open(%r, 'w').close()" % str(marker)
    return dict(args=[name, "-c", code], executable=sys.executable)


def test_control_harmless_command_still_runs(tmp_path):
    marker = tmp_path / "ran"
    subprocess.run(**_placeholder("harmless-tool", marker), check=True, timeout=30)
    assert marker.exists(), "the placeholder construction itself does not run"


@pytest.mark.parametrize("name", ["pw-record", "parecord", "parec", "arecord", "rec"])
def test_real_recorder_start_is_blocked_and_never_runs(name, tmp_path, capture_guard):
    marker = tmp_path / "ran"
    with capture_guard.expect() as seen:
        with pytest.raises(capture_guard.Blocked, match="real audio capture device"):
            subprocess.Popen(**_placeholder(name, marker))
    assert not marker.exists(), "the recorder was really started"
    assert len(seen) == 1 and name in seen[0] and "test_real_recorder" in seen[0]


def test_blocked_start_via_run_and_swallowing_caller(tmp_path, capture_guard):
    """subprocess.run geht denselben Weg; fängt der Produktcode die Ausnahme,
    bleibt der Verstoß vermerkt (der Teardown würde den Test rot machen)."""
    marker = tmp_path / "ran"
    with capture_guard.expect() as seen:
        try:
            subprocess.run(**_placeholder("pw-record", marker), timeout=30)
        except Exception:                         # noqa: BLE001 — wie der Produktcode
            pass
    assert len(seen) == 1 and not marker.exists()


_SWALLOWING_TEST = """
import subprocess, sys

def test_swallowing_caller():
    try:
        subprocess.run(["pw-record", "-c", "pass"], executable=sys.executable)
    except Exception:
        pass
"""


def test_swallowed_violation_still_fails_the_test(tmp_path):
    """Fängt der Produktcode die Sperre ab, scheitert der Test im Teardown. Ein
    eigener pytest-Lauf mit derselben conftest.py belegt das."""
    d = tmp_path / "tests"
    d.mkdir()
    shutil.copy(os.path.join(ROOT, "tests", "conftest.py"), d / "conftest.py")
    (d / "test_swallow.py").write_text(_SWALLOWING_TEST)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(d)],
        capture_output=True, text=True, timeout=120, cwd=str(tmp_path),
        env={**os.environ, "PYTHONPATH": ROOT, "QUASSEL_LIVE_TESTS": ""})
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "real audio capture was attempted" in out, out
    assert "test_swallow.py::test_swallowing_caller" in out, out


@pytest.mark.parametrize("argv, expected", [
    (["pw-record", "--rate", "16000", "-"], True),
    (["/usr/bin/parecord", "--raw"], True),
    (["C:\\tools\\arecord.exe", "-f", "S16_LE"], True),
    (["pw-cat", "--record", "-"], True),
    (["pw-cat", "-r", "-"], True),
    (["pw-cat", "--playback", "x.wav"], False),
    (["rec", "-r", "16000", "x.wav"], True),
    (["sox", "-d", "x.wav"], True),
    (["sox", "-t", "alsa", "default", "x.wav"], True),
    (["sox", "in.wav", "out.wav"], False),
    (["ffmpeg", "-f", "avfoundation", "-i", ":0", "out.wav"], True),
    (["ffmpeg", "-f", "pulse", "-i", "default", "out.wav"], True),
    (["ffmpeg", "-f", "alsa", "-i", "hw:0", "out.wav"], True),
    (["ffmpeg", "-f", "dshow", "-i", "audio=Mic", "out.wav"], True),
    (["ffmpeg", "-i", "in.wav", "-f", "alsa", "default"], False),
    (["ffmpeg", "-nostdin", "-y", "-i", "a.mp3", "-f", "wav", "b.wav"], False),
    (["env", "X=1", "timeout", "5", "pw-record", "-"], True),
    (["/bin/sh", "-c", "pw-record --rate 16000 - | head -c 10"], True),
    (["/bin/sh", "-c", "echo pw-record"], False),
    ("pw-record --rate 16000 -", True),
    ("echo hello | parecord --raw", True),
    (["pactl", "list", "sources"], False),
    (["git", "status"], False),
    ([sys.executable, "-c", "pass"], False),
])
def test_command_classifier(argv, expected, capture_guard):
    assert bool(capture_guard.reason(argv)) is expected


def test_recording_tool_named_only_by_executable_is_caught(capture_guard):
    assert capture_guard.reason(["x"], executable="/usr/bin/pw-record")


# ---------------------------------------------------------------- sounddevice
_FAKE_SD = textwrap.dedent('''
    """Stand-in package: opens "the device" (creates a marker) in __init__."""
    import os
    MARKER = os.environ["FAKE_SD_MARKER"]

    class _Base:
        def __init__(self, *a, **kw):
            open(MARKER, "a").close()

    class InputStream(_Base): pass
    class RawInputStream(_Base): pass
    class Stream(InputStream): pass
    class RawStream(RawInputStream): pass
    class OutputStream(_Base): pass

    def rec(*a, **kw):
        InputStream()

    def playrec(*a, **kw):
        Stream()
''')


@pytest.fixture
def fake_sounddevice(tmp_path, monkeypatch):
    """Ein sounddevice, das beim Öffnen einer Eingabe eine Datei anlegt; wird über
    den echten Importweg geladen (der Schutz hängt am Import)."""
    pkg = tmp_path / "fakesd"
    pkg.mkdir()
    (pkg / "sounddevice.py").write_text(_FAKE_SD)
    marker = tmp_path / "device-opened"
    monkeypatch.setenv("FAKE_SD_MARKER", str(marker))
    monkeypatch.syspath_prepend(str(pkg))
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    importlib.invalidate_caches()
    yield marker
    sys.modules.pop("sounddevice", None)


def test_sounddevice_not_imported_by_the_guard():
    """Der Schutz darf PortAudio nicht anstoßen: ohne Import durch den Code
    taucht sounddevice nicht in sys.modules auf."""
    code = ("import sys; sys.path.insert(0, %r); import conftest; "
            "print('sounddevice' in sys.modules)" % os.path.join(ROOT, "tests"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60, check=True,
                         env={**os.environ, "QUASSEL_LIVE_TESTS": ""})
    assert out.stdout.strip().endswith("False"), out.stdout + out.stderr


@pytest.mark.parametrize("cls", ["InputStream", "RawInputStream", "Stream", "RawStream"])
def test_sounddevice_input_stream_is_blocked(cls, fake_sounddevice, capture_guard):
    import sounddevice as sd
    with capture_guard.expect() as seen:
        with pytest.raises(capture_guard.Blocked, match="sounddevice.%s" % cls):
            getattr(sd, cls)(samplerate=16000, channels=1)
    assert not fake_sounddevice.exists(), "the input device was opened"
    assert len(seen) == 1


@pytest.mark.parametrize("func", ["rec", "playrec"])
def test_sounddevice_rec_is_blocked(func, fake_sounddevice, capture_guard):
    import sounddevice as sd
    with capture_guard.expect() as seen:
        with pytest.raises(capture_guard.Blocked):
            getattr(sd, func)(16000, samplerate=16000, channels=1)
    assert not fake_sounddevice.exists()
    assert len(seen) == 1


def test_sounddevice_output_stream_is_left_alone(fake_sounddevice):
    """Nur die Eingabe wird gesperrt; die Attrappe zeigt, dass der Rest lebt."""
    import sounddevice as sd
    sd.OutputStream()
    assert fake_sounddevice.exists()


def test_test_fakes_for_sounddevice_still_work(monkeypatch):
    """Attrappen, die Tests selbst einsetzen, bleiben unberührt."""
    from quassel import audio

    class Fake:
        def RawInputStream(self, **kw):          # noqa: N802
            return "fake-stream"
    monkeypatch.setattr(audio, "_sd", lambda: Fake())
    assert audio._sd().RawInputStream(samplerate=16000) == "fake-stream"


if sys.platform == "darwin" and not _OPT_IN:
    def test_real_sounddevice_input_is_blocked_on_the_mac(capture_guard):
        """Auf dem Mac ist das der Weg, über den die App aufnimmt (sounddevice/
        CoreAudio); das echte Modul, kein Stellvertreter. Es wird nur importiert,
        nie ein Gerät geöffnet."""
        import sounddevice as sd
        with capture_guard.expect() as seen:
            for cls in ("InputStream", "RawInputStream", "Stream", "RawStream"):
                with pytest.raises(capture_guard.Blocked):
                    getattr(sd, cls)(samplerate=16000, channels=1)
            with pytest.raises(capture_guard.Blocked):
                sd.rec(16000, samplerate=16000, channels=1)
        assert len(seen) == 5

    def test_mac_recorder_cannot_open_the_real_microphone(monkeypatch, tmp_path,
                                                          capture_guard):
        """Ende-zu-Ende auf dem Mac: der Recorder auf dem macOS-Pfad mit dem
        echten (unersetzten) sounddevice scheitert an der Sperre statt Audio zu
        öffnen. Der Produktcode fängt die Ausnahme; der Verstoß bleibt vermerkt."""
        from quassel import audio
        monkeypatch.setenv("QUASSEL_MAC_AUDIO", "sd16")
        rec = audio.Recorder(raw_path=str(tmp_path / "x.raw"), preroll_ms=0)
        with capture_guard.expect() as seen:
            started = rec.start("default")
            rec.stop()
        assert started is False
        assert seen and "sounddevice.RawInputStream" in seen[0]


# ------------------------------------------------------------------ CoreAudio
def test_no_coreaudio_input_path_in_the_code():
    """quassel/coreaudio.py öffnet nur die Default-Output-AudioUnit. Taucht ein
    Eingabe-Symbol im Produktcode auf, gibt es einen Aufnahmeweg, den dieser
    Schutz nicht sieht: dann hier einen Hook ergänzen, statt die Liste zu kürzen."""
    banned = ("kAudioOutputUnitProperty_EnableIO", "AudioDeviceCreateIOProcID",
              "AudioDeviceStart", "AudioQueueNewInput", "AVCaptureSession",
              "AVAudioEngine", "AVCaptureAudioDataOutput", "inputNode")
    hits = []
    pkg = os.path.join(ROOT, "quassel")
    for dirpath, _dirs, files in os.walk(pkg):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(dirpath, f)
                text = open(path, encoding="utf-8").read()
                hits += ["%s: %s" % (os.path.relpath(path, ROOT), b)
                         for b in banned if b in text]
    assert not hits, "CoreAudio/AVFoundation capture symbols found:\n" + "\n".join(hits)
