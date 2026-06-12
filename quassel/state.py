"""Laufzeit-Dateien + State-IPC Daemon → Pille (state.json, atomar)."""
import json
import os
import time

if os.name == "nt":
    RUNDIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                          "Quassel", "run")
else:
    XDG_RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    RUNDIR = os.path.join(XDG_RUNTIME, "quassel")
RAW = os.path.join(RUNDIR, "rec.raw")
WAV = os.path.join(RUNDIR, "rec.wav")
PARTWAV = os.path.join(RUNDIR, "partial.wav")
STATE = os.path.join(RUNDIR, "state.json")


def state_set(state, text=""):
    os.makedirs(RUNDIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"state": state, "text": text, "ts": time.time()}, f)
    os.replace(tmp, STATE)


def state_read():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"state": "idle", "text": "", "ts": 0}
