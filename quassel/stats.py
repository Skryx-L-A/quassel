"""Lokale Nutzungsstatistik von Quassel (Issue #22).

Zaehlt diktierte Woerter, Sitzungen, Zeichen und geschaetzte gesparte
Tippzeit. Alles bleibt ausschliesslich lokal als JSON-Datei und wird
niemals irgendwohin gesendet.

Speicherort: <DATADIR>/stats.json (wie history.jsonl).
Fuer Tests ueberschreibbar via Umgebungsvariable QUASSEL_STATS_PATH.
"""
import datetime
import json
import os
import time

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
    "seconds_spoken": 0.0,
    "first_ts": None,
    "last_ts": None,
    # Tagesbuckets nach LOKALER Zeit: "YYYY-MM-DD" -> {"words","seconds","sessions"}
    "daily": {},
}


def _empty():
    d = dict(_EMPTY)
    d["daily"] = {}
    return d


def _load():
    """Liest die Statistik; bei fehlender/kaputter Datei leere Zaehler."""
    try:
        with open(_stats_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    out = _empty()
    for k in _EMPTY:
        if k in data:
            out[k] = data[k]
    if not isinstance(out.get("daily"), dict):
        out["daily"] = {}
    return out


def _local_day(ts):
    """Lokales Datum 'YYYY-MM-DD' eines Timestamps ueber die SYSTEM-Zeitzone.

    time.localtime nutzt die lokale Zeitzone des Rechners (TZ/etc-localtime),
    ganz ohne Internet; ist keine gesetzt, ist es effektiv die Systemuhr."""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _save(data):
    path = _stats_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record(text, *, seconds=0.0, typing_wpm=40):
    """Verbucht ein abgeschlossenes Diktat.

    sessions += 1, words += Wortanzahl, chars += len(text).
    seconds_saved += words / typing_wpm * 60 (Zeit, um so viele Woerter
    von Hand bei typing_wpm zu tippen). seconds_spoken += ``seconds`` (echte
    Sprechdauer, sofern bekannt). Zudem wird ein Tagesbucket der LOKALEN
    Zeitzone fortgeschrieben. first_ts einmalig, last_ts immer.
    """
    words = len(text.split())
    data = _load()
    now = time.time()
    data["sessions"] += 1
    data["words"] += words
    data["chars"] += len(text)
    if typing_wpm > 0:
        data["seconds_saved"] += words / typing_wpm * 60
    try:
        data["seconds_spoken"] = data.get("seconds_spoken", 0.0) + float(seconds or 0.0)
    except (TypeError, ValueError):
        pass
    day = _local_day(now)
    bucket = data["daily"].get(day) or {"words": 0, "seconds": 0.0, "sessions": 0}
    bucket["words"] += words
    bucket["seconds"] = bucket.get("seconds", 0.0) + float(seconds or 0.0)
    bucket["sessions"] += 1
    data["daily"][day] = bucket
    if data["first_ts"] is None:
        data["first_ts"] = now
    data["last_ts"] = now
    _save(data)


def summary():
    """Aktuelle Zaehler als dict; Nullen/None ohne Datei."""
    return _load()


def words_today(s=None):
    """Heute (lokale Zeit) gesprochene Woerter."""
    if s is None:
        s = _load()
    day = _local_day(time.time())
    return (s.get("daily", {}).get(day) or {}).get("words", 0)


def daily_series(days=14, s=None):
    """Liste der letzten ``days`` Tage (lokal) als (datum, woerter), Nullen aufgefuellt.

    Aeltester Tag zuerst, heute zuletzt — direkt fuer ein Balkendiagramm."""
    if s is None:
        s = _load()
    daily = s.get("daily", {})
    today = datetime.date.fromtimestamp(time.time())
    out = []
    for i in range(days - 1, -1, -1):
        d = today - datetime.timedelta(days=i)
        key = d.isoformat()
        out.append((key, (daily.get(key) or {}).get("words", 0)))
    return out


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
