"""macOS-Tonausgabe über CoreAudio/AudioToolbox — nur so weit, wie die
Signaltöne es brauchen.

Warum überhaupt eine eigene Anbindung, wo die Aufnahme schon über sounddevice
läuft: PortAudio ermittelt das Standard-Ausgabegerät EINMAL beim Initialisieren
und merkt sich auch die Geräteliste. Stellt der Nutzer danach den System-Standard
um oder verbindet er Kopfhörer, sieht PortAudio davon nichts mehr. Gemessen am
2026-08-04 auf diesem Mac:

    System-Standard-Ausgabe VORHER      id=86 'Bluetooth-Kopfhörer'
    PortAudio default-out beim Start    idx=2 'Bluetooth-Kopfhörer'
    System-Standard-Ausgabe JETZT       id=72 'MacBook Pro-Lautsprecher'
    PortAudio default-out ohne Neu-Init idx=2 'Bluetooth-Kopfhörer'          <- eingefroren
    PortAudio default-out NACH Neu-Init idx=4 'MacBook Pro-Lautsprecher'

und für ein Gerät, das erst nach dem Start auftaucht:

    Aggregat für PortAudio sichtbar OHNE Neu-Init: False
    Aggregat sichtbar NACH Neu-Init:               True

Ein Neu-Init ist kein Ausweg: `Pa_Terminate` reißt alle offenen Ströme mit, und
der Startton kommt genau dann, wenn der Aufnahmestrom offen ist.

Die Default-Output-AudioUnit von CoreAudio hat das Problem nicht. Sie folgt dem
System-Standard von sich aus, auch mitten im Betrieb — gemessen mit derselben
Umstellung wie oben, während die Unit lief:

    System-Standard umgestellt auf: id=72 'MacBook Pro-Lautsprecher'
    AudioUnit spielt jetzt auf   : id=72 'MacBook Pro-Lautsprecher'
    Render läuft weiter: True (54 -> 83 Callbacks)

Deshalb laufen die Töne hier und nicht mehr über PortAudio. Nebenbei fällt das
Aushandeln der Abtastrate weg: die Unit nimmt unser Format (16 kHz, Mono, int16)
an und wandelt selbst.

Das Modul lädt die Frameworks erst beim ersten Gebrauch und meldet Fehler als
None oder leere Liste — ein Ton darf nie etwas aufhalten.
"""
import ctypes
import threading

# Frameworks liegen auf jedem macOS an fester Stelle. ctypes.util.find_library
# wäre der übliche Weg, ist im PyInstaller-Bundle aber schon ausgefallen; der
# absolute Pfad ist hier die verlässlichere Angabe.
_AUDIOTOOLBOX = "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox"
_COREAUDIO = "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
_COREFOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

_LOAD_LOCK = threading.Lock()
_LIBS = None            # (AudioToolbox, CoreAudio, CoreFoundation) oder False


def _fourcc(s):
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


# CoreAudio-Selektoren und -Bereiche
_SYSTEM_OBJECT = 1
_SEL_DEFAULT_OUTPUT = _fourcc("dOut")
_SEL_DEVICES = _fourcc("dev#")
_SEL_NAME = _fourcc("lnam")
_SEL_STREAMS = _fourcc("stm#")
_SCOPE_GLOBAL = _fourcc("glob")
_SCOPE_OUTPUT = _fourcc("outp")

# AudioUnit-Konstanten
_TYPE_OUTPUT = _fourcc("auou")
_SUBTYPE_DEFAULT_OUTPUT = _fourcc("def ")
_MANUFACTURER_APPLE = _fourcc("appl")
_PROP_STREAM_FORMAT = 8
_PROP_SET_RENDER_CALLBACK = 23
_PROP_CURRENT_DEVICE = 2000
_SCOPE_UNIT_GLOBAL = 0
_SCOPE_UNIT_INPUT = 1
_FORMAT_LINEAR_PCM = _fourcc("lpcm")
_FLAG_SIGNED_INTEGER = 0x4
_FLAG_PACKED = 0x8

_UTF8 = 0x08000100      # kCFStringEncodingUTF8


class _Addr(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32),
                ("scope", ctypes.c_uint32),
                ("element", ctypes.c_uint32)]


class _ComponentDescription(ctypes.Structure):
    _fields_ = [("componentType", ctypes.c_uint32),
                ("componentSubType", ctypes.c_uint32),
                ("componentManufacturer", ctypes.c_uint32),
                ("componentFlags", ctypes.c_uint32),
                ("componentFlagsMask", ctypes.c_uint32)]


class _StreamFormat(ctypes.Structure):
    """AudioStreamBasicDescription."""
    _fields_ = [("sample_rate", ctypes.c_double),
                ("format_id", ctypes.c_uint32),
                ("format_flags", ctypes.c_uint32),
                ("bytes_per_packet", ctypes.c_uint32),
                ("frames_per_packet", ctypes.c_uint32),
                ("bytes_per_frame", ctypes.c_uint32),
                ("channels_per_frame", ctypes.c_uint32),
                ("bits_per_channel", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32)]


class _AudioBuffer(ctypes.Structure):
    _fields_ = [("channels", ctypes.c_uint32),
                ("byte_size", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


class _AudioBufferList(ctypes.Structure):
    # CoreAudio liefert eine Liste variabler Länge. Mehr als eine Handvoll
    # Puffer kann bei einem Mono-Kanal nicht kommen; acht sind reichlich und
    # vermeiden eine Struktur mit variabler Größe.
    _fields_ = [("count", ctypes.c_uint32),
                ("buffers", _AudioBuffer * 8)]


class _RenderCallbackStruct(ctypes.Structure):
    _fields_ = [("proc", ctypes.c_void_p),
                ("ref_con", ctypes.c_void_p)]


_RENDER_PROC = ctypes.CFUNCTYPE(ctypes.c_int32,          # OSStatus
                                ctypes.c_void_p,         # inRefCon
                                ctypes.POINTER(ctypes.c_uint32),   # ioActionFlags
                                ctypes.c_void_p,         # inTimeStamp
                                ctypes.c_uint32,         # inBusNumber
                                ctypes.c_uint32,         # inNumberFrames
                                ctypes.POINTER(_AudioBufferList))


def _libs():
    """(AudioToolbox, CoreAudio, CoreFoundation) oder None. Einmal geladen,
    danach gemerkt — auch das Scheitern."""
    global _LIBS
    with _LOAD_LOCK:
        if _LIBS is None:
            try:
                _LIBS = (ctypes.CDLL(_AUDIOTOOLBOX),
                         ctypes.CDLL(_COREAUDIO),
                         ctypes.CDLL(_COREFOUNDATION))
            except OSError:
                _LIBS = False
        return _LIBS or None


def available():
    """True, wenn die Frameworks geladen werden konnten."""
    return _libs() is not None


# ------------------------------------------------------------ Geräteabfrage
def _get_uint32(obj, selector, scope=_SCOPE_GLOBAL):
    libs = _libs()
    if libs is None:
        return None
    addr = _Addr(selector, scope, 0)
    size = ctypes.c_uint32(4)
    value = ctypes.c_uint32(0)
    err = libs[1].AudioObjectGetPropertyData(ctypes.c_uint32(obj),
                                             ctypes.byref(addr), 0, None,
                                             ctypes.byref(size),
                                             ctypes.byref(value))
    return None if err else value.value


def _property_size(obj, selector, scope=_SCOPE_GLOBAL):
    libs = _libs()
    if libs is None:
        return 0
    addr = _Addr(selector, scope, 0)
    size = ctypes.c_uint32(0)
    err = libs[1].AudioObjectGetPropertyDataSize(ctypes.c_uint32(obj),
                                                 ctypes.byref(addr), 0, None,
                                                 ctypes.byref(size))
    return 0 if err else size.value


def default_output_device():
    """AudioDeviceID des System-Standard-Ausgabegeräts oder None."""
    return _get_uint32(_SYSTEM_OBJECT, _SEL_DEFAULT_OUTPUT)


def device_name(device):
    """Anzeigename eines Geräts oder None."""
    libs = _libs()
    if libs is None:
        return None
    addr = _Addr(_SEL_NAME, _SCOPE_GLOBAL, 0)
    ref = ctypes.c_void_p()
    size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    err = libs[1].AudioObjectGetPropertyData(ctypes.c_uint32(device),
                                             ctypes.byref(addr), 0, None,
                                             ctypes.byref(size),
                                             ctypes.byref(ref))
    if err or not ref.value:
        return None
    buf = ctypes.create_string_buffer(512)
    cf = libs[2]
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                      ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_ubyte    # CFStringGetCString gibt Boolean
    ok = cf.CFStringGetCString(ref, buf, 512, _UTF8)
    cf.CFRelease(ctypes.c_void_p(ref.value))
    if not ok:
        return None
    try:
        return buf.value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _all_devices():
    libs = _libs()
    if libs is None:
        return []
    count = _property_size(_SYSTEM_OBJECT, _SEL_DEVICES) // 4
    if count <= 0:
        return []
    addr = _Addr(_SEL_DEVICES, _SCOPE_GLOBAL, 0)
    arr = (ctypes.c_uint32 * count)()
    size = ctypes.c_uint32(ctypes.sizeof(arr))
    err = libs[1].AudioObjectGetPropertyData(ctypes.c_uint32(_SYSTEM_OBJECT),
                                             ctypes.byref(addr), 0, None,
                                             ctypes.byref(size),
                                             ctypes.byref(arr))
    return [] if err else list(arr)


def output_devices():
    """[(AudioDeviceID, Name)] aller Geräte, die überhaupt etwas ausgeben
    können. Anders als bei PortAudio ist die Liste bei jedem Aufruf frisch."""
    out = []
    for dev in _all_devices():
        if _property_size(dev, _SEL_STREAMS, _SCOPE_OUTPUT) <= 0:
            continue
        name = device_name(dev)
        if name:
            out.append((dev, name))
    return out


def find_output_device(name):
    """AudioDeviceID des Ausgabegeräts mit diesem Namen, oder None. Tragen
    zwei Geräte denselben Namen, gewinnt das erste — dieselbe Auflösung, die
    der Mikrofon-Picker seit jeher benutzt."""
    if not name:
        return None
    for dev, dev_name in output_devices():
        if dev_name == name:
            return dev
    return None


def _set_default_output(device):
    """Setzt das System-Standard-Ausgabegerät. Quassel ruft das NIE im Betrieb
    auf — die App ändert keine Systemeinstellungen. Die Funktion gibt es allein
    für den Hardware-Test, der nachweist, dass die Ausgabe-Einheit einem
    Wechsel folgt (tests/test_coreaudio_mac.py, nur mit
    QUASSEL_AUDIO_HW_TEST=1)."""
    libs = _libs()
    if libs is None:
        return False
    addr = _Addr(_SEL_DEFAULT_OUTPUT, _SCOPE_GLOBAL, 0)
    value = ctypes.c_uint32(device)
    err = libs[1].AudioObjectSetPropertyData(ctypes.c_uint32(_SYSTEM_OBJECT),
                                             ctypes.byref(addr), 0, None,
                                             ctypes.c_uint32(4),
                                             ctypes.byref(value))
    return not err


# --------------------------------------------------------- Ausgabe-Einheit
class DefaultOutputUnit:
    """Warmgehaltene Default-Output-AudioUnit.

    `callback(memoryview)` wird auf dem Audio-Thread gerufen und MUSS den
    übergebenen Puffer vollständig füllen (mit Ton oder mit Stille) und sofort
    zurückkehren. Ohne `device` folgt die Unit dem System-Standard, auch wenn
    er sich im Betrieb ändert; mit `device` bleibt sie an genau diesem Gerät.
    """

    def __init__(self, callback, rate, device=None):
        self._callback = callback
        self._unit = None
        # Die ctypes-Hülle muss am Objekt hängen bleiben: wird sie eingesammelt,
        # ruft CoreAudio in freigegebenen Speicher.
        self._proc = _RENDER_PROC(self._render)
        self._started = False
        libs = _libs()
        if libs is None:
            raise OSError("CoreAudio nicht ladbar")
        self._at = libs[0]
        self._open(rate, device)

    # -- Aufbau
    def _open(self, rate, device):
        at = self._at
        desc = _ComponentDescription(_TYPE_OUTPUT, _SUBTYPE_DEFAULT_OUTPUT,
                                     _MANUFACTURER_APPLE, 0, 0)
        at.AudioComponentFindNext.restype = ctypes.c_void_p
        comp = at.AudioComponentFindNext(None, ctypes.byref(desc))
        if not comp:
            raise OSError("keine Default-Output-AudioUnit gefunden")
        unit = ctypes.c_void_p()
        self._check(at.AudioComponentInstanceNew(ctypes.c_void_p(comp),
                                                 ctypes.byref(unit)),
                    "AudioComponentInstanceNew")
        self._unit = unit
        try:
            if device is not None:
                # Ein festes Gerät statt des System-Standards: ab hier folgt
                # die Unit dem Standard bewusst NICHT mehr.
                dev = ctypes.c_uint32(device)
                self._check(at.AudioUnitSetProperty(unit, _PROP_CURRENT_DEVICE,
                                                    _SCOPE_UNIT_GLOBAL, 0,
                                                    ctypes.byref(dev), 4),
                            "CurrentDevice")
            fmt = _StreamFormat(float(rate), _FORMAT_LINEAR_PCM,
                                _FLAG_SIGNED_INTEGER | _FLAG_PACKED,
                                2, 1, 2, 1, 16, 0)
            self._check(at.AudioUnitSetProperty(unit, _PROP_STREAM_FORMAT,
                                                _SCOPE_UNIT_INPUT, 0,
                                                ctypes.byref(fmt),
                                                ctypes.sizeof(fmt)),
                        "StreamFormat")
            cb = _RenderCallbackStruct(ctypes.cast(self._proc, ctypes.c_void_p).value,
                                       None)
            self._check(at.AudioUnitSetProperty(unit, _PROP_SET_RENDER_CALLBACK,
                                                _SCOPE_UNIT_INPUT, 0,
                                                ctypes.byref(cb),
                                                ctypes.sizeof(cb)),
                        "SetRenderCallback")
            self._check(at.AudioUnitInitialize(unit), "AudioUnitInitialize")
        except Exception:
            self.close()
            raise

    def _check(self, err, what):
        if err:
            raise OSError("%s fehlgeschlagen (OSStatus %d)" % (what, err))

    # -- Betrieb
    def start(self):
        self._check(self._at.AudioOutputUnitStart(self._unit),
                    "AudioOutputUnitStart")
        self._started = True

    @property
    def active(self):
        return self._started and self._unit is not None

    def current_device(self):
        """Gerät, auf dem die Unit gerade spielt (zum Prüfen und Loggen)."""
        if self._unit is None:
            return None
        dev = ctypes.c_uint32(0)
        size = ctypes.c_uint32(4)
        err = self._at.AudioUnitGetProperty(self._unit, _PROP_CURRENT_DEVICE,
                                            _SCOPE_UNIT_GLOBAL, 0,
                                            ctypes.byref(dev),
                                            ctypes.byref(size))
        return None if err else dev.value

    def _render(self, _ref, _flags, _timestamp, _bus, _frames, io_data):
        """Audio-Thread. Reicht jeden Puffer an den Aufrufer weiter; kommt der
        nicht durch, wird der Puffer HIER genullt. CoreAudio übergibt keinen
        geleerten Puffer — was darin steht, ist der Inhalt des letzten Durchgangs
        und käme als Krachen aus dem Lautsprecher. Kehrt immer mit 0 zurück: ein
        Fehler würde die Einheit anhalten, und Stille ist besser als eine tote
        Ausgabe.

        Das `.cast("B")` ist nicht kosmetisch. Ein memoryview auf ein
        `c_char`-Array hat das Format `<c`, und darauf ist Slice-Zuweisung in
        CPython nicht implementiert: `out[:] = ...` wirft dann
        `NotImplementedError: memoryview: unsupported format <c`. Genau das ist
        beim ersten Umbau passiert — die Einheit lief, die Callbacks kamen, die
        Gerätewahl stimmte, und kein einziger Ton wurde je in den Puffer
        geschrieben. Ein Review hat es gefunden, kein Test: die Attrappe reichte
        ein bytearray durch, auf dem die Zuweisung funktioniert. Sie reicht
        heute denselben Puffertyp durch wie CoreAudio."""
        try:
            data = io_data.contents
            for i in range(min(data.count, len(data.buffers))):
                buf = data.buffers[i]
                if not buf.data or not buf.byte_size:
                    continue
                raw = (ctypes.c_char * buf.byte_size).from_address(buf.data)
                view = memoryview(raw).cast("B")
                try:
                    self._callback(view)
                except Exception:  # noqa: BLE001 — dann eben Stille
                    view[:] = b"\x00" * buf.byte_size
        except Exception:      # noqa: BLE001 — auf dem Audio-Thread wird nie geworfen
            pass
        return 0

    def close(self):
        unit, self._unit = self._unit, None
        self._started = False
        if unit is None:
            return
        at = self._at
        try:
            at.AudioOutputUnitStop(unit)
            at.AudioUnitUninitialize(unit)
            at.AudioComponentInstanceDispose(unit)
        except Exception:      # noqa: BLE001 — Abbau darf nie stören
            pass

    def __del__(self):
        """Rettungsleine für eine Einheit, die jemand fallen lässt, ohne sie zu
        schließen. Instanz und Callback-Hülle halten sich gegenseitig, der
        Zyklus ist also einsammelbar — und würde CoreAudio mit einem
        Funktionszeiger auf freigegebenen Speicher zurücklassen. Im Betrieb
        kommt es nicht dazu (beep._MacPlayer schließt immer), das hier ist für
        den nächsten Aufrufer."""
        try:
            self.close()
        except Exception:      # noqa: BLE001 — beim Aufräumen ist alles erlaubt außer werfen
            pass
