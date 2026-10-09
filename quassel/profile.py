"""Profil-Schalter: eine zweite Quassel-Fassung neben der installierten.

Ohne QUASSEL_PROFILE ändert sich nichts. Mit QUASSEL_PROFILE=next bekommt die
Instanz eigene Verzeichnisse (Konfiguration, Daten, Laufzeit, Log) und einen
eigenen Server-Port, damit sie der installierten App weder Einstellungen noch
den whisper-server wegnimmt. Die Modelle bleiben geteilt (nur lesend): sie
sind mehrere Gigabyte groß und ändern sich durch das Profil nicht.
"""
import os
import re
import zlib

BASE_PORT = 8765
_VALID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def name():
    """Profilname oder "" für die Standardinstanz. Ungültige Namen zählen als
    Standard — aus dem Namen werden Pfade gebaut."""
    raw = os.environ.get("QUASSEL_PROFILE", "").strip().lower()
    return raw if _VALID.match(raw) else ""


def suffix():
    n = name()
    return f"-{n}" if n else ""


def service(base="quassel-server"):
    """systemd-Unit dieser Instanz: die Standardinstanz nutzt die feste Unit,
    ein Profil die Vorlage systemd/quassel-server@.service."""
    n = name()
    return f"{base}@{n}.service" if n else f"{base}.service"


def dirname(base):
    """"Quassel" -> "Quassel-next" (bzw. "quassel" -> "quassel-next")."""
    return base + suffix()


def port():
    """Standardinstanz 8765. Ein Profil bekommt einen festen eigenen Port aus
    seinem Namen (8766..8865); QUASSEL_PORT überschreibt beides."""
    env = os.environ.get("QUASSEL_PORT", "").strip()
    if env.isdigit() and 1024 <= int(env) <= 65535:
        return int(env)
    n = name()
    if not n:
        return BASE_PORT
    return BASE_PORT + 1 + zlib.crc32(n.encode()) % 100
