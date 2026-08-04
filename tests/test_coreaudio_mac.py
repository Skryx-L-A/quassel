"""Tests der CoreAudio-Anbindung gegen die echte Audio-Hardware dieses Macs.

Diese Tests öffnen wirklich eine Ausgabe-Einheit, spielen aber ausschließlich
STILLE: der Render-Callback schreibt Nullen und zählt nur mit, wie oft er
gerufen wurde. Es kommt kein hörbarer Ton aus dem Gerät.

Außerhalb von macOS werden sie übersprungen, ebenso auf einer Maschine ohne
Ausgabegerät — beides sind gültige Umgebungen, nur eben keine, in der sich die
Anbindung prüfen lässt.

Der Test, der den System-Standard tatsächlich umstellt, läuft nur mit
QUASSEL_AUDIO_HW_TEST=1. Er verändert eine Systemeinstellung (und stellt sie
danach wieder her), und das darf keine beiläufige Nebenwirkung eines
Testlaufs sein.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import coreaudio

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="CoreAudio gibt es nur auf macOS")


@pytest.fixture
def geraete():
    devs = coreaudio.output_devices()
    if not devs:
        pytest.skip("kein Ausgabegerät vorhanden")
    return devs


class Zaehler:
    """Render-Callback, der nur Stille schreibt und mitzählt.

    Er merkt sich JEDEN Fehlschlag beim Schreiben. Das ist der Punkt: eine
    frühere Fassung zählte nur die Aufrufe, und weil der Zähler vor der
    Zuweisung hochgeht und `_render` deren Ausnahme abfängt, war der Test grün,
    während in Wahrheit kein einziges Byte in den Puffer kam."""

    def __init__(self):
        self.n = 0
        self.bytes = 0
        self.fehler = []
        self.geschrieben = 0

    def __call__(self, out):
        self.n += 1
        self.bytes += len(out)
        try:
            out[:] = b"\x00" * len(out)
            self.geschrieben += len(out)
        except Exception as exc:      # noqa: BLE001 — genau darum geht es hier
            self.fehler.append(repr(exc))


def _unit(device=None):
    zaehler = Zaehler()
    unit = coreaudio.DefaultOutputUnit(zaehler, 16000, device)
    unit.start()
    return unit, zaehler


def _warte_auf_render(zaehler, timeout=2.0):
    ende = time.monotonic() + timeout
    while time.monotonic() < ende and zaehler.n == 0:
        time.sleep(0.01)
    return zaehler.n


# ------------------------------------------------------------- Geräteabfrage
def test_frameworks_sind_ladbar():
    assert coreaudio.available()


def test_standardausgabe_ist_ein_ausgabegeraet(geraete):
    default = coreaudio.default_output_device()
    assert default
    assert default in [dev for dev, _name in geraete]


def test_geraete_haben_namen(geraete):
    for dev, name in geraete:
        assert isinstance(name, str) and name


def test_find_output_device_findet_den_standard(geraete):
    default = coreaudio.default_output_device()
    name = coreaudio.device_name(default)
    assert name
    # Bei doppelten Namen gewinnt das erste Gerät; verlangt wird nur, dass die
    # Auflösung überhaupt ein Ausgabegerät mit diesem Namen liefert.
    gefunden = coreaudio.find_output_device(name)
    assert gefunden in [dev for dev, dev_name in geraete if dev_name == name]


def test_unbekanntes_geraet_gibt_none(geraete):
    assert coreaudio.find_output_device("Gibt Es Ganz Sicher Nicht") is None
    assert coreaudio.find_output_device("") is None
    assert coreaudio.find_output_device(None) is None


# ------------------------------------------------------------ Ausgabe-Einheit
def test_einheit_ohne_geraet_spielt_auf_dem_systemstandard(geraete):
    """Der Kern des Fehlers aus v2.6.0: PortAudio hielt hier das Gerät fest,
    das beim Prozessstart Standard war. Die Default-Output-AudioUnit nimmt das
    Gerät, das JETZT Standard ist."""
    unit, zaehler = _unit()
    try:
        assert _warte_auf_render(zaehler) > 0        # Einheit läuft wirklich
        assert unit.current_device() == coreaudio.default_output_device()
        assert unit.active
    finally:
        unit.close()
    assert not unit.active


def test_einheit_mit_geraet_bleibt_an_diesem_geraet(geraete):
    dev, _name = geraete[0]
    unit, zaehler = _unit(dev)
    try:
        assert _warte_auf_render(zaehler) > 0
        assert unit.current_device() == dev
    finally:
        unit.close()


def test_geschlossene_einheit_meldet_kein_geraet(geraete):
    unit, _zaehler = _unit()
    unit.close()
    assert unit.current_device() is None
    unit.close()                                     # zweimal schließen ist erlaubt


def test_callback_bekommt_beschreibbare_puffer(geraete):
    """Der Callback MUSS den Puffer füllen dürfen — täte er es nicht, käme
    Rauschen aus nicht initialisiertem Speicher, und ein Ton, den niemand
    schreibt, ist kein Ton. Geprüft wird nicht, DASS der Callback gerufen
    wurde, sondern dass die Zuweisung durchging."""
    unit, zaehler = _unit()
    try:
        assert _warte_auf_render(zaehler) > 0
        assert zaehler.bytes > 0
        assert zaehler.fehler == []
        assert zaehler.geschrieben == zaehler.bytes
    finally:
        unit.close()


# ------------------------------------------- echter Wechsel des Systemstandards
@pytest.mark.skipif(os.environ.get("QUASSEL_AUDIO_HW_TEST") != "1",
                    reason="verändert den System-Standard; nur mit "
                           "QUASSEL_AUDIO_HW_TEST=1")
def test_einheit_folgt_dem_wechsel_des_systemstandards(geraete):
    """Der Nachweis für den eigentlichen Fehlerbericht: der Nutzer stellt das
    Ausgabegerät um (oder verbindet Kopfhörer), und die Töne müssen mitgehen.

    Der Test stellt den System-Standard um und danach in jedem Fall zurück."""
    if len(geraete) < 2:
        pytest.skip("nur ein Ausgabegerät")
    vorher = coreaudio.default_output_device()
    ziel = next(dev for dev, _name in geraete if dev != vorher)
    unit, zaehler = _unit()
    try:
        assert _warte_auf_render(zaehler) > 0
        assert unit.current_device() == vorher

        coreaudio._set_default_output(ziel)
        gewechselt = False
        ende = time.monotonic() + 3.0
        while time.monotonic() < ende:
            if unit.current_device() == ziel:
                gewechselt = True
                break
            time.sleep(0.05)
        assert gewechselt, "Einheit folgte dem neuen System-Standard nicht"

        vorher_n = zaehler.n
        time.sleep(0.3)
        assert zaehler.n > vorher_n, "Einheit hörte nach dem Wechsel auf zu spielen"
    finally:
        coreaudio._set_default_output(vorher)
        unit.close()
