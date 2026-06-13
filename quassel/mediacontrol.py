"""Audio-Ducking während des Diktierens.

Modi:
  * "music" — gerade spielende Medien-/Musik-Player pausieren (und danach
              wieder fortsetzen).
  * "all"   — den Gesamtton stummschalten (und danach wiederherstellen).

Wird beim Aufnahme-Start angewandt und beim Aufnahme-Ende wiederhergestellt,
damit kein Ton ins Mikrofon gerät oder ablenkt.

Das plattformspezifische Backend muss zwei Funktionen bereitstellen:
    duck_apply(mode) -> token        (token wird unverändert an restore gereicht)
    duck_restore(mode, token) -> None
Linux-Backend: ``quassel.platform_linux``. Windows-Backend: ``quassel.win.audioctl``.
Fehlt das Backend oder ein Tool, sind alle Operationen wirkungslos (kein Fehler):
Audio-Steuerung darf das Diktat niemals stören.
"""
import os

try:
    if os.name == "nt":
        from .win import audioctl as _backend
    else:
        from . import platform_linux as _backend
except Exception:        # noqa: BLE001 — Backend ist optional
    _backend = None


def _call(name, *args):
    fn = getattr(_backend, name, None) if _backend else None
    if fn is None:
        return None
    try:
        return fn(*args)
    except Exception:    # noqa: BLE001 — Audio-Tools dürfen nie das Diktat stören
        return None


class AudioDucker:
    """Schaltet Musik/Ton beim Diktieren leise und stellt es danach wieder her.

    Zustandsbehaftet: ``apply(mode)`` merkt sich, was zurückzunehmen ist, und
    ``restore()`` macht es rückgängig. Mehrfaches ``apply`` ohne ``restore``
    stellt zuerst den vorherigen Zustand wieder her (kein Leak)."""

    VALID = ("music", "all")

    def __init__(self):
        self._mode = "off"
        self._token = None

    def apply(self, mode):
        if self._mode in self.VALID:     # offene Wiederherstellung zuerst abschließen
            self.restore()
        if mode not in self.VALID:
            self._mode, self._token = "off", None
            return
        self._mode = mode
        self._token = _call("duck_apply", mode)

    def restore(self):
        if self._mode in self.VALID:
            _call("duck_restore", self._mode, self._token)
        self._mode, self._token = "off", None
