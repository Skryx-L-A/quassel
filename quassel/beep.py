"""Kurze Töne für Aufnahme-Start/-Ende (plattformneutral, optional abschaltbar).

Zwei UNTERSCHIEDLICHE Töne: aufsteigend = "du kannst sprechen" (Start),
absteigend = "fertig, Aufnahme aus" (Stop). Bewusst leise + kurz. Das Abspielen
ist nicht-blockierend und schlägt leise fehl, wenn kein Player/Datei da ist —
ein Ton darf das Diktat nie aufhalten.

Linux: pw-play / paplay / aplay (das erste vorhandene). Windows: winsound.
"""
import os
import shutil
import subprocess
import sys


def _sounds_dir():
    mei = getattr(sys, "_MEIPASS", None)        # PyInstaller-Bundle
    cands = []
    if mei:
        cands.append(os.path.join(mei, "assets", "sounds"))
    cands.append(os.path.join(os.path.dirname(__file__), "..", "assets", "sounds"))
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[-1]


_DIR = _sounds_dir()
START_WAV = os.path.join(_DIR, "start.wav")
STOP_WAV = os.path.join(_DIR, "stop.wav")


def _play(path):
    if not path or not os.path.exists(path):
        return
    try:
        if os.name == "nt":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        for player in (["pw-play"], ["paplay"], ["aplay", "-q"]):
            if shutil.which(player[0]):
                subprocess.Popen(player + [path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    except Exception:        # noqa: BLE001 — Ton darf nie stören
        pass


def start():
    """Aufsteigender Ton: Aufnahme bereit, jetzt sprechen."""
    _play(START_WAV)


def stop():
    """Absteigender Ton: Aufnahme beendet."""
    _play(STOP_WAV)
