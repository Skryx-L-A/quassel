"""Lokale Nutzungsstatistik von Quassel (Issue #22).

Zaehlt diktierte Woerter, Sitzungen, Zeichen und geschaetzte gesparte
Tippzeit. Alles bleibt ausschliesslich lokal als JSON-Datei und wird
niemals irgendwohin gesendet.

Speicherort: <DATADIR>/stats.json (wie history.jsonl).
Fuer Tests ueberschreibbar via Umgebungsvariable QUASSEL_STATS_PATH.
"""
import json
import os

from quassel import config


def _stats_path():
    """Pfad der Statistikdatei, zur Laufzeit ermittelt.

    Wenn QUASSEL_STATS_PATH gesetzt ist, hat sie Vorrang (fuer Tests),
    sonst <DATADIR>/stats.json.
    """
    override = os.environ.get("QUASSEL_STATS_PATH")
    if override:
        return override
    return os.path.join(config.DATADIR, "stats.json")


# Modulvariable nur zur Anzeige/Doku; die Funktionen lesen den Pfad
# bewusst lazy ueber _stats_path(), damit Tests die Env spaeter setzen koennen.
STATS_PATH = os.path.join(config.DATADIR, "stats.json")

_EMPTY = {
    "sessions": 0,
    "words": 0,
    "chars": 0,
    "seconds_saved": 0.0,
    "first_ts": None,
    "last_ts": None,
}


def _load():
    """Liest die Statistik; bei fehlender/kaputter Datei leere Zaehler."""
    try:
        with open(_stats_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(_EMPTY)
    if not isinstance(data, dict):
        return dict(_EMPTY)
    out = dict(_EMPTY)
    for k in _EMPTY:
        if k in data:
            out[k] = data[k]
    return out


def _save(data):
    path = _stats_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record(text, *, typing_wpm=40):
    """Verbucht ein abgeschlossenes Diktat.

    sessions += 1, words += Wortanzahl, chars += len(text).
    seconds_saved += words / typing_wpm * 60 (Zeit, um so viele Woerter
    von Hand bei typing_wpm zu tippen). first_ts einmalig, last_ts immer.
    """
    import time

    words = len(text.split())
    data = _load()
    now = time.time()
    data["sessions"] += 1
    data["words"] += words
    data["chars"] += len(text)
    if typing_wpm > 0:
        data["seconds_saved"] += words / typing_wpm * 60
    if data["first_ts"] is None:
        data["first_ts"] = now
    data["last_ts"] = now
    _save(data)


def summary():
    """Aktuelle Zaehler als dict; Nullen/None ohne Datei."""
    return _load()


def format_summary(s=None):
    """Menschenlesbare Einzeile, reines ASCII, keine Emojis.

    z. B. "1,234 words in 56 sessions - about 31 min of typing saved".
    """
    if s is None:
        s = summary()
    words = s.get("words", 0)
    sessions = s.get("sessions", 0)
    minutes = int(round(s.get("seconds_saved", 0.0) / 60))
    return (
        f"{words:,} words in {sessions:,} sessions"
        f" - about {minutes:,} min of typing saved"
    )


def reset():
    """Loescht die Statistikdatei (Zaehler auf Null). Kein Fehler, wenn weg."""
    try:
        os.remove(_stats_path())
    except OSError:
        pass
