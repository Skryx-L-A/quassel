"""Laufzeit-Dateien + State-IPC Daemon → Pille (state.json, atomar)."""
import json
import os
import sys
import time

from . import profile

if os.name == "nt":
    RUNDIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                          profile.dirname("Quassel"), "run")
elif sys.platform == "darwin":
    # macOS kennt kein XDG_RUNTIME_DIR. Nicht in ~/Library/Caches: das darf
    # das System bei Plattenplatzdruck jederzeit leeren — auch mitten in
    # einer laufenden Aufnahme (rec.raw).
    RUNDIR = os.path.expanduser(
        "~/Library/Application Support/" + profile.dirname("Quassel") + "/run")
else:
    XDG_RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    RUNDIR = os.path.join(XDG_RUNTIME, profile.dirname("quassel"))
RAW = os.path.join(RUNDIR, "rec.raw")
WAV = os.path.join(RUNDIR, "rec.wav")
PARTWAV = os.path.join(RUNDIR, "partial.wav")
STATE = os.path.join(RUNDIR, "state.json")


def _write(state, text, ts):
    os.makedirs(RUNDIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"state": state, "text": text, "ts": ts}, f)
    os.replace(tmp, STATE)


def state_set(state, text=""):
    """Zustand schreiben. Rückgabe: der Zeitstempel als Kennung dieser
    Meldung (für state_clear_text)."""
    ts = time.time()
    _write(state, text, ts)
    return ts


def state_clear_text(ts):
    """Den Text einer angezeigten Meldung aus state.json entfernen (Audit A6:
    das letzte Diktat bleibt sonst als Klartext auf der Platte und in Backups).
    Nur, wenn seither nichts Neues gemeldet wurde; Zustand und Zeitstempel
    bleiben, damit die Pille nichts neu zeichnet. True = geleert."""
    st = state_read()
    if st.get("ts") != ts or not st.get("text"):
        return False
    try:
        _write(st.get("state", "idle"), "", ts)
    except OSError:
        return False
    return True


def state_read():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"state": "idle", "text": "", "ts": 0}
