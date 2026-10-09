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


class InvalidProfile(SystemExit):
    """Ungültiger QUASSEL_PROFILE oder ein Port, der der installierten App
    gehört: der Prozess startet nicht, statt still die Standardinstanz zu
    benutzen (Pfade, Port 8765, deren systemd-Units)."""


def name():
    """Profilname oder "" für die Standardinstanz. Ein gesetzter, aber
    ungültiger Name beendet den Start (aus dem Namen werden Pfade gebaut;
    früher fiel er still auf die installierte Instanz zurück)."""
    raw = os.environ.get("QUASSEL_PROFILE", "").strip().lower()
    if raw and not _VALID.match(raw):
        raise InvalidProfile(
            "QUASSEL_PROFILE=%r is invalid: use a-z, 0-9 and '-', at most 32 characters"
            % raw)
    return raw


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
    n = name()
    if env.isdigit() and 1024 <= int(env) <= 65535:
        if n and int(env) == BASE_PORT:
            raise InvalidProfile(
                "QUASSEL_PORT=%d belongs to the installed Quassel; a profile needs its own port"
                % BASE_PORT)
        return int(env)
    if not n:
        return BASE_PORT
    return BASE_PORT + 1 + zlib.crc32(n.encode()) % 100
