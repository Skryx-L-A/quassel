"""In-App-Update-Check: vergleicht die installierte Version mit dem neuesten
GitHub-Release von Skryx-L-A/quassel und meldet, ob eine neuere Version vorliegt.

Netzwerk-Fehler werden immer verschluckt: keine Funktion wirft je an den Aufrufer.
Nur Standardbibliothek (urllib, json, re, threading).
"""
import json
import re
import threading
import urllib.request

REPO = "Skryx-L-A/quassel"

_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(v: str) -> tuple[int, int, int]:
    """Parse "2.2.0", "v2.2.0", "2.2.0-beta" -> (2, 2, 0).

    Toleriert ein fuehrendes 'v' und ignoriert jeden Pre-Release-/Build-Suffix
    nach der Patch-Zahl. Fehlende Teile sind 0. Nicht-numerisch -> (0, 0, 0).
    """
    if not isinstance(v, str):
        return (0, 0, 0)
    m = _VERSION_RE.match(v)
    if not m:
        return (0, 0, 0)
    major = int(m.group(1)) if m.group(1) else 0
    minor = int(m.group(2)) if m.group(2) else 0
    patch = int(m.group(3)) if m.group(3) else 0
    return (major, minor, patch)


def compare_versions(a: str, b: str) -> int:
    """-1 wenn a<b, 0 wenn gleich, 1 wenn a>b (nach major, minor, patch)."""
    pa, pb = parse_version(a), parse_version(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def is_newer(current: str, latest: str) -> bool:
    """True genau dann, wenn latest > current."""
    return compare_versions(latest, current) > 0


def fetch_latest_tag(repo: str = REPO, timeout: float = 4.0) -> str | None:
    """Hole das neueste Release-Tag von GitHub; None bei jedem Fehler/Timeout.

    Das Netzwerk darf nie an den Aufrufer werfen.
    """
    url = "https://api.github.com/repos/{0}/releases/latest".format(repo)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "quassel-updatecheck",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name")
        if isinstance(tag, str) and tag:
            return tag
        return None
    except Exception:
        return None


def check(current_version: str, repo: str = REPO, timeout: float = 4.0) -> dict:
    """{"current": ..., "latest": <tag oder None>, "update_available": bool}.

    update_available ist False, wenn latest None ist. Fehler werden verschluckt.
    """
    latest = fetch_latest_tag(repo, timeout)
    update_available = latest is not None and is_newer(current_version, latest)
    return {
        "current": current_version,
        "latest": latest,
        "update_available": update_available,
    }


def check_async(current_version, callback, repo=REPO, timeout=4.0) -> None:
    """Fuehre check() in einem Daemon-Thread aus und rufe callback(result_dict).

    Genutzt von der GUI, damit der Netzwerk-Aufruf die UI nie blockiert.
    """
    def _run():
        callback(check(current_version, repo, timeout))

    threading.Thread(target=_run, daemon=True).start()
