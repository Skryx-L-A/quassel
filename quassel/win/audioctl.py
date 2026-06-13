"""Windows-Backend fuer quassel.mediacontrol (Audio-Ducking beim Diktieren).

Vertrag (siehe quassel/mediacontrol.py):
    duck_apply(mode) -> token        token wandert unveraendert zu restore
    duck_restore(mode, token) -> None

  mode == "all"   : Gesamtton stummschalten ueber Windows Core Audio (pycaw);
                    Fallback ist ein VK_VOLUME_MUTE-Tastendruck (toggelt).
  mode == "music" : gerade spielende Medien-Sessions pausieren ueber SMTC/WinRT
                    (winsdk); Fallback ist ein VK_MEDIA_PLAY_PAUSE-Tastendruck.

Alles ist best-effort: jede Operation laeuft in try/except und wird zur
No-op, wenn ein Backend oder Geraet fehlt — die Audio-Steuerung darf das
Diktat niemals stoeren. Die Token sind kleine dicts, die festhalten, was
zurueckzunehmen ist (z.B. war der Ton schon vor dem Diktat stumm, bleibt er
stumm; eine bereits pausierte Session bleibt pausiert).
"""
import ctypes
import ctypes.wintypes as wt

VK_VOLUME_MUTE = 0xAD
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

# Core Audio (Master stummschalten). Optional — fehlt pycaw, greift der
# Tastendruck-Fallback. Modulweite Importe, damit PyInstaller sie einsammelt.
try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _HAVE_PYCAW = True
except Exception:        # noqa: BLE001 — Backend optional
    _HAVE_PYCAW = False

# SMTC/WinRT (spielende Medien pausieren). Optional — fehlt die Projektion,
# greift der Tastendruck-Fallback. Bevorzugt das gepflegte winrt-Paket
# (pywinrt, mit Wheels fuer aktuelle Python-Versionen); winsdk als Rueckfall
# fuer aeltere Umgebungen. Beide haben dieselbe SMTC-API.
try:
    import winrt.windows.media.control as _smtc
    from winrt.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _PLAYBACK
    _HAVE_SMTC = True
except Exception:        # noqa: BLE001
    try:
        import winsdk.windows.media.control as _smtc
        from winsdk.windows.media.control import \
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as _PLAYBACK
        _HAVE_SMTC = True
    except Exception:    # noqa: BLE001 — Backend optional
        _HAVE_SMTC = False


# ----------------------------------------------------------- Tastendruck (Fallback)
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", _INPUTUNION)]


def _tap(vk):
    """Eine Multimedia-Taste druecken und loslassen (via SendInput)."""
    arr = (_INPUT * 2)()
    for i, flag in enumerate((0, KEYEVENTF_KEYUP)):
        arr[i].type = INPUT_KEYBOARD
        arr[i].union.ki = _KEYBDINPUT(vk, 0, flag, 0, None)
    ctypes.windll.user32.SendInput(2, arr, ctypes.sizeof(_INPUT))


# ----------------------------------------------------------- "all": Gesamtton
def _endpoint_volume():
    """IAudioEndpointVolume des Standard-Ausgabegeraets (GetMute/SetMute).

    Neuere pycaw-Versionen liefern ein AudioDevice mit fertiger
    EndpointVolume-Property; aeltere ein IMMDevice, das erst aktiviert wird."""
    dev = AudioUtilities.GetSpeakers()
    ev = getattr(dev, "EndpointVolume", None)
    if ev is not None:
        return ev
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


# ----------------------------------------------------------- "music": SMTC/WinRT
def _run_async(coro):
    import asyncio
    return asyncio.run(coro)


def _smtc_pause_playing():
    """Pausiert jede gerade spielende Session; gibt deren AppIds zurueck."""
    async def _work():
        mgr = await _smtc.GlobalSystemMediaTransportControlsSessionManager \
            .request_async()
        appids = []
        for s in mgr.get_sessions():
            try:
                if s.get_playback_info().playback_status == _PLAYBACK.PLAYING:
                    await s.try_pause_async()
                    appids.append(s.source_app_user_model_id)
            except Exception:    # noqa: BLE001 — einzelne Session ueberspringen
                continue
        return appids
    return _run_async(_work()) or []


def _smtc_play(appids):
    if not appids:
        return
    wanted = set(appids)

    async def _work():
        mgr = await _smtc.GlobalSystemMediaTransportControlsSessionManager \
            .request_async()
        for s in mgr.get_sessions():
            try:
                if s.source_app_user_model_id in wanted:
                    await s.try_play_async()
            except Exception:    # noqa: BLE001
                continue
    _run_async(_work())


# ----------------------------------------------------------- Vertrag
def duck_apply(mode):
    if mode == "all":
        if _HAVE_PYCAW:
            try:
                vol = _endpoint_volume()
                was_muted = bool(vol.GetMute())
                vol.SetMute(1, None)
                return {"all": True, "was_muted": was_muted}
            except Exception:    # noqa: BLE001 — auf Tastendruck zurueckfallen
                pass
        _tap(VK_VOLUME_MUTE)                      # toggelt -> Restore toggelt erneut
        return {"all": True, "fallback": True}
    if mode == "music":
        if _HAVE_SMTC:
            try:
                return {"music": True, "appids": _smtc_pause_playing()}
            except Exception:    # noqa: BLE001 — auf Tastendruck zurueckfallen
                pass
        _tap(VK_MEDIA_PLAY_PAUSE)                 # pausiert die aktive Session
        return {"music": True, "fallback": True}
    return None


def duck_restore(mode, token):
    if not token:
        return
    if token.get("all"):
        if token.get("fallback"):
            _tap(VK_VOLUME_MUTE)                  # zurueck-toggeln
        elif not token.get("was_muted") and _HAVE_PYCAW:
            # nur entstummen, wenn der Ton vor dem Diktat NICHT stumm war
            try:
                _endpoint_volume().SetMute(0, None)
            except Exception:    # noqa: BLE001
                pass
    elif token.get("music"):
        if token.get("fallback"):
            _tap(VK_MEDIA_PLAY_PAUSE)             # fortsetzen
        else:
            _smtc_play(token.get("appids", []))
