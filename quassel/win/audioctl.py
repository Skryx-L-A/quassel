"""Windows-Backend fuer quassel.mediacontrol (Audio-Ducking beim Diktieren).

Vertrag (siehe quassel/mediacontrol.py):
    duck_apply(mode) -> token        token wandert unveraendert zu restore
    duck_restore(mode, token) -> None

  mode == "all"   : Gesamtton stummschalten ueber Windows Core Audio (pycaw);
                    ohne Statusabfrage bleibt die Ausgabe unveraendert.
  mode == "music" : gerade spielende Medien-Sessions pausieren ueber SMTC/WinRT
                    (winsdk); ohne Statusabfrage bleibt die Wiedergabe unveraendert.

Alles ist best-effort: jede Operation laeuft in try/except und wird zur
No-op, wenn ein Backend oder Geraet fehlt — die Audio-Steuerung darf das
Diktat niemals stoeren. Die Token sind kleine dicts, die festhalten, was
zurueckzunehmen ist (z.B. war der Ton schon vor dem Diktat stumm, bleibt er
stumm; eine bereits pausierte Session bleibt pausiert).
"""
# Core Audio (Master stummschalten). Optional — fehlt pycaw, greift der
# Modulweite Importe, damit PyInstaller sie einsammelt.
try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    _HAVE_PYCAW = True
except Exception:        # noqa: BLE001 — Backend optional
    _HAVE_PYCAW = False

# SMTC/WinRT (spielende Medien pausieren). Optional — fehlt die Projektion,
# bleibt Ducking aus. Bevorzugt das gepflegte winrt-Paket
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
            except Exception:    # noqa: BLE001 — ohne Status keinen Toggle senden
                pass
        return None
    if mode == "music":
        if _HAVE_SMTC:
            try:
                return {"music": True, "appids": _smtc_pause_playing()}
            except Exception:    # noqa: BLE001 — ohne Status keinen Toggle senden
                pass
        return None
    return None


def duck_restore(mode, token):
    if not token:
        return
    if token.get("all"):
        if not token.get("was_muted") and _HAVE_PYCAW:
            # nur entstummen, wenn der Ton vor dem Diktat NICHT stumm war
            try:
                _endpoint_volume().SetMute(0, None)
            except Exception:    # noqa: BLE001
                pass
    elif token.get("music"):
        _smtc_play(token.get("appids", []))
