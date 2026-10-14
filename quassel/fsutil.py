"""Dateisystem-Helfer, die keine Abhängigkeit auf andere Quassel-Module haben."""
import os
import threading
import time

# Windows: os.replace scheitert mit WinError 5 / 32, solange ein anderer Schreiber
# oder Leser (Pille, Virenscanner, Indexer) das Ziel im selben Augenblick öffnet
# oder ersetzt. Das ist kurz; ein paar Wiederholungen genügen.
_REPLACE_TRIES = 20
_REPLACE_PAUSE = 0.01


def replace(tmp, path):
    """os.replace mit Wiederholung bei kurzer Windows-Sperre."""
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_PAUSE)


def atomic_write(path, text, *, mode=None, encoding="utf-8"):
    """Schreibt text atomar nach path.

    Ein einfaches open(path, "w") kürzt die Datei sofort auf null und füllt sie
    erst danach. Stirbt der Prozess in diesem Fenster, oder geht der Strom weg,
    bleibt eine leere oder halbe Datei zurück, und die Einstellung, das
    Wörterbuch oder der Verlauf ist still weg. Hier wird erst eine Temp-Datei
    im selben Verzeichnis vollständig geschrieben und auf die Platte gebracht,
    dann mit os.replace über das Ziel gelegt: sichtbar ist immer entweder die
    alte oder die vollständige neue Datei. Schlägt etwas fehl, bleibt die alte
    Datei stehen und die Temp-Datei wird entfernt.

    Der Name der Temp-Datei trägt Prozess- und Thread-Nummer, damit zwei
    Schreiber (Daemon und Kontrollzentrum) nicht dieselbe Datei durchmischen.

    mode: Dateirechte der neuen Datei (z. B. 0o600). Ohne Angabe behält die
    Datei die Rechte der bisherigen, eine neue bekommt die üblichen.
    Fehlende Verzeichnisse werden angelegt."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    flags = (os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL
             | getattr(os, "O_BINARY", 0))
    keep_mode = None
    if mode is None and os.name != "nt":
        try:
            keep_mode = os.stat(path).st_mode & 0o7777
        except OSError:
            pass
    try:
        fd = os.open(tmp, flags, 0o666 if mode is None else mode)
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if keep_mode is not None:
            os.chmod(tmp, keep_mode)
        replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
