"""Gemeinsame Test-Fixtures.

Per-App-Profile (quassel/profiles.py) liest sonst die echte profiles.ini des
Nutzers. Legt jemand eine an, fragten alle Diktat-Tests plötzlich die echte
aktive Anwendung ab und verhielten sich je nach Fenster anders. Darum zeigt
PROFILES in jedem Test auf eine Datei, die es nicht gibt. Tests, die Profile
brauchen, setzen PROFILES selbst (monkeypatch überschreibt diesen Wert).
"""
import atexit
import os
import shutil
import sys
import tempfile

import pytest

# Alle Pfade von quassel (Konfiguration, Wörterbuch, Verlauf, Laufzeitordner)
# werden beim Import aus dem Home berechnet. Ein Fixture, das nur einen davon
# umbiegt, lässt die anderen auf der echten Installation (so überschrieb ein
# Test das echte Wörterbuch). Darum zeigt das Home schon vor dem ersten Import
# von quassel auf einen Wegwerfordner; tests/test_isolation.py prüft das.
# Windows: expanduser liest USERPROFILE, nicht HOME; Quassel nutzt dort APPDATA.
os.environ.setdefault("QUASSEL_TEST_REAL_HOME", os.path.expanduser("~"))
_TEST_HOME = tempfile.mkdtemp(prefix="quassel-test-home-")
atexit.register(shutil.rmtree, _TEST_HOME, True)
os.environ["HOME"] = os.environ["USERPROFILE"] = _TEST_HOME
for _var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
             "APPDATA", "LOCALAPPDATA"):
    os.environ.pop(_var, None)
if os.name == "nt":
    os.environ["APPDATA"] = os.path.join(_TEST_HOME, "AppData", "Roaming")
    os.environ["LOCALAPPDATA"] = os.path.join(_TEST_HOME, "AppData", "Local")
# Linux: Laufzeitordner (Sockets, PID-Dateien) liegt sonst unter /run/user/<uid>.
os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TEST_HOME, "run")
os.makedirs(os.environ["XDG_RUNTIME_DIR"], mode=0o700)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ------------------------------------------------ keine echte Aufnahme im Test
# Ein macOS-Mikrofontest startete unter Linux die echte Aufnahme (pw-record),
# weil der Produktcode sys.platform statt IS_MAC prüfte. Kein Test darf je ein
# echtes Aufnahmegerät öffnen, auch wenn Produktcode oder Attrappe falsch sind.
# Der Schutz sitzt darum unterhalb jeder Attrappe, die ein Test setzen kann:
#   * subprocess.Popen._execute_child (POSIX und Windows) — jeder Prozessstart,
#     auch wenn ein Test subprocess.run/Popen nicht selbst ersetzt;
#   * sounddevice: Import-Hook, der nach dem Import die Eingabe-Streams sperrt.
#     sounddevice wird hier NIE importiert (der Import initialisiert PortAudio
#     und braucht libportaudio).
# CoreAudio: quassel/coreaudio.py öffnet nur die Default-Output-AudioUnit; einen
# Eingabepfad gibt es nicht (test_no_real_capture.py hält das mit einer
# Quelltextprobe fest).
# Ein Verstoß wirft RealCaptureBlocked UND wird vermerkt; fängt der Produktcode
# die Ausnahme (except Exception), scheitert der Test trotzdem im Teardown.
# Ausnahme nur mit ausdrücklichem Opt-in QUASSEL_LIVE_TESTS=1.
CAPTURE_GUARD_ACTIVE = os.environ.get("QUASSEL_LIVE_TESTS") != "1"

_CAPTURE_TOOLS = {"pw-record", "parecord", "parec", "arecord", "rec"}
# nehmen nur mit Zusatzflag auf: pw-cat/pacat --record (-r), sonst Wiedergabe
_CAPTURE_IF_RECORD_FLAG = {"pw-cat", "pacat"}
_AUDIO_INPUT_FORMATS = {"avfoundation", "pulse", "alsa", "dshow", "oss", "jack",
                        "sndio", "openal", "pipewire"}
_SOX_DEVICE_TYPES = {"alsa", "pulseaudio", "coreaudio", "waveaudio", "oss", "sndio"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "cmd", "powershell", "pwsh"}
_WRAPPERS = {"env", "timeout", "nice", "nohup", "stdbuf", "sudo", "setsid", "exec",
             "ionice", "chrt", "taskset"}
_SD_INPUT_CLASSES = ("InputStream", "RawInputStream", "Stream", "RawStream")
_SD_INPUT_FUNCS = ("rec", "playrec")

_capture_violations = []


class RealCaptureBlocked(RuntimeError):
    """Ein Test (oder der Code darunter) wollte ein echtes Aufnahmegerät öffnen."""


def _record_violation(what):
    test = os.environ.get("PYTEST_CURRENT_TEST", "<outside a test>")
    msg = ("%s: tried to open a real audio capture device (%s). Tests must use a "
           "fake recorder; QUASSEL_LIVE_TESTS=1 is the only way to allow this."
           % (test, what))
    _capture_violations.append(msg)
    raise RealCaptureBlocked(msg)


def _tool_name(token):
    # beide Trenner: ein Windows-Pfad kann auch auf POSIX geprüft werden
    name = str(token).replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _split_command(cmd):
    """Zeile in Wörter; ein Parserfehler darf den Start nicht verdecken."""
    import shlex
    try:
        return shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        return cmd.split()


def capture_command_reason(args, executable=None, _depth=0):
    """Wofür dieser Prozessstart ein Aufnahmegerät öffnen würde, sonst None."""
    if isinstance(args, (str, bytes, os.PathLike)):
        args = os.fsdecode(args)
        words = _split_command(args)
        # Pipeline/Liste von Befehlen: jedes Segment einzeln prüfen
        segments, cur = [], []
        for w in words:
            if w in ("|", ";", "&&", "||", "&"):
                segments.append(cur)
                cur = []
            else:
                cur.append(w)
        segments.append(cur)
        if len(segments) > 1:
            for seg in segments:
                why = seg and capture_command_reason(seg, _depth=_depth + 1)
                if why:
                    return why
            return None
        args = words
    argv = [os.fsdecode(a) for a in args]
    if executable is not None and _tool_name(executable) in _CAPTURE_TOOLS:
        return "executable %s" % executable
    # Wrapper (env, timeout, nice ...) überspringen: Optionen, VAR=x, Zahlen
    while argv and _tool_name(argv[0]) in _WRAPPERS:
        argv = argv[1:]
        while argv and (argv[0].startswith("-") or "=" in argv[0]
                        or argv[0].replace(".", "", 1).rstrip("smhd").isdigit()):
            argv = argv[1:]
    if not argv:
        return None
    name = _tool_name(argv[0])
    rest = argv[1:]
    if name in _SHELLS and _depth < 4:
        for i, a in enumerate(rest):
            if a.lower() in ("-c", "/c", "-command", "/k") and i + 1 < len(rest):
                why = capture_command_reason(rest[i + 1], _depth=_depth + 1)
                if why:
                    return why
        return None
    if name in _CAPTURE_TOOLS:
        return "recording tool %s" % name
    if name in _CAPTURE_IF_RECORD_FLAG and ("--record" in rest or "-r" in rest):
        return "%s --record" % name
    if name == "sox":
        for i, a in enumerate(rest):
            if a in ("-d", "--default-device"):
                return "sox default audio device"
            if a == "-t" and i + 1 < len(rest) and rest[i + 1] in _SOX_DEVICE_TYPES:
                return "sox device type %s" % rest[i + 1]
    if name in ("ffmpeg", "ffprobe", "ffplay", "avconv"):
        # nur ein "-f <Gerät>" VOR dem ersten -i ist ein Eingabegerät; dahinter
        # ist es das Ausgabeformat (Wiedergabe)
        for i, a in enumerate(rest):
            if a == "-i":
                break
            if a == "-f" and i + 1 < len(rest) and rest[i + 1] in _AUDIO_INPUT_FORMATS:
                return "ffmpeg input device -f %s" % rest[i + 1]
    return None


def _guard_popen():
    import subprocess
    original = subprocess.Popen._execute_child

    def _execute_child(self, args, executable=None, *a, **kw):
        why = capture_command_reason(args, executable)
        if why:
            _record_violation("%s: %r" % (why, args))
        return original(self, args, executable, *a, **kw)

    subprocess.Popen._execute_child = _execute_child


def _guard_sounddevice_module(module):
    """Stream-Klassen und rec()/playrec() so sperren, dass die Eingabe nie öffnet."""
    def blocked(what):
        def _blocked(*a, **kw):
            _record_violation("sounddevice.%s" % what)
        return _blocked

    for name in _SD_INPUT_CLASSES:
        cls = getattr(module, name, None)
        if isinstance(cls, type):
            cls.__init__ = (lambda n: lambda self, *a, **kw: blocked(n)())(name)
    for name in _SD_INPUT_FUNCS:
        if hasattr(module, name):
            setattr(module, name, blocked(name))


class _GuardedLoader:
    """Hüllt den echten Loader von sounddevice und sperrt nach dem Import."""

    def __init__(self, real):
        self._real = real

    def create_module(self, spec):
        return self._real.create_module(spec)

    def exec_module(self, module):
        self._real.exec_module(module)
        _guard_sounddevice_module(module)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _SoundDeviceFinder:
    """Meta-Path-Finder: findet sounddevice über die übrigen Finder, setzt aber
    den gehüllten Loader ein. Wird nur beim tatsächlichen Import aktiv."""

    def __init__(self):
        import threading
        self._busy = threading.local()

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "sounddevice" or getattr(self._busy, "on", False):
            return None
        import importlib.util
        self._busy.on = True
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            self._busy.on = False
        if spec is not None and spec.loader is not None:
            spec.loader = _GuardedLoader(spec.loader)
        return spec


def _install_capture_guard():
    _guard_popen()
    sys.meta_path.insert(0, _SoundDeviceFinder())
    if "sounddevice" in sys.modules:     # schon importiert (Plugin o. ä.)
        _guard_sounddevice_module(sys.modules["sounddevice"])


if CAPTURE_GUARD_ACTIVE:
    _install_capture_guard()


class _CaptureGuardHandle:
    active = CAPTURE_GUARD_ACTIVE
    violations = _capture_violations
    reason = staticmethod(capture_command_reason)
    Blocked = RealCaptureBlocked

    @staticmethod
    def expect():
        """Verstöße im with-Block sind gewollt (Schutztest): sie werden danach
        entfernt, damit der Teardown den Test nicht als Verstoß wertet."""
        import contextlib

        @contextlib.contextmanager
        def _cm():
            start = len(_capture_violations)
            seen = []
            try:
                yield seen
            finally:
                seen.extend(_capture_violations[start:])
                del _capture_violations[start:]
        return _cm()


@pytest.fixture
def capture_guard():
    return _CaptureGuardHandle


@pytest.fixture(autouse=True)
def _no_real_capture():
    yield
    if _capture_violations:
        msgs = list(_capture_violations)
        del _capture_violations[:]
        pytest.fail("real audio capture was attempted (the code may have caught "
                    "the exception):\n  " + "\n  ".join(msgs), pytrace=False)


@pytest.fixture(scope="session")
def _no_profiles_path(tmp_path_factory):
    return str(tmp_path_factory.mktemp("no-profiles") / "profiles.ini")


@pytest.fixture(autouse=True)
def _isolated_profiles(monkeypatch, _no_profiles_path):
    from quassel import profiles
    monkeypatch.setattr(profiles, "PROFILES", _no_profiles_path)
    profiles._cache["key"] = None
    yield
    profiles._cache["key"] = None
