"""Tests des macOS-Abspielwegs (beep._MacPlayer) ohne echtes Audiogerät.

Kein Test öffnet ein Gerät und keiner erzeugt hörbaren Ton: gespielt wird
ausschließlich gegen die Attrappe der Ausgabe-Einheit, die WAV-Dateien liegen
unter tmp_path. Den Audio-Thread spielt der Test selbst nach — pump() ruft den
Render-Callback so oft auf, wie eine laufende Einheit es täte. Threads, Queue
und Fristen sind echt.

Der Kern dieser Datei ist die Gerätefrage: die Töne müssen auf dem
System-Standard-Ausgabegerät landen und einem Wechsel folgen. Bis v2.6.0 taten
sie das nicht (PortAudio friert das Standardgerät beim Prozessstart ein), und
genau dafür stehen die Tests im Abschnitt „Gerätewahl".
"""
import ctypes
import os
import struct
import sys
import threading
import time
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import beep, coreaudio


# ------------------------------------------------------------------ Doubles
class FakeUnit:
    """Ersatz für coreaudio.DefaultOutputUnit. Die in `hang` genannten Aufrufe
    kehren nicht zurück, bis release() sie freigibt — so wie ein
    Bluetooth-Gerät, das nach dem Wegfallen der Verbindung nicht mehr
    antwortet."""

    def __init__(self, callback, rate, device, hang=(), blocksize=128):
        self.callback = callback
        self.rate = rate
        self.device = device
        self.blocksize = blocksize
        self.calls = []
        self.out = bytearray()             # alles, was der Callback ausgab
        self.active = False
        self._hang = set(hang)
        self._block = threading.Event()

    def start(self):
        self.calls.append("start")
        self.active = True
        if "start" in self._hang:
            self._block.wait(30)

    def pump(self, blocks=1):
        """Den Audio-Thread nachspielen: `blocks` Blöcke abholen lassen.

        Der Weg geht bewusst durch das ECHTE `DefaultOutputUnit._render` und
        über einen ctypes-Puffer, wie ihn CoreAudio übergibt. Die erste Fassung
        dieser Attrappe reichte ein bytearray durch — darauf funktioniert
        Slice-Zuweisung, auf dem echten Puffer nicht, und so blieben sieben
        Tests grün, während im Betrieb kein einziger Ton geschrieben wurde. Der
        Puffer ist mit 0xff vorbelegt: was hier als Null ankommt, hat der
        Callback wirklich geschrieben."""
        for _ in range(blocks):
            n = self.blocksize * 2                  # int16-Mono
            raw = (ctypes.c_char * n)()
            ctypes.memset(raw, 0xFF, n)
            liste = coreaudio._AudioBufferList()
            liste.count = 1
            liste.buffers[0] = coreaudio._AudioBuffer(
                1, n, ctypes.cast(raw, ctypes.c_void_p))
            echt = coreaudio.DefaultOutputUnit.__new__(coreaudio.DefaultOutputUnit)
            echt._callback = self.callback
            echt._render(None, None, None, 0, self.blocksize,
                         ctypes.pointer(liste))
            self.out.extend(bytes(raw))
        return bytes(self.out)

    def close(self):
        self.calls.append("close")
        self.active = False
        if "close" in self._hang:
            self._block.wait(30)

    def release(self):
        self._block.set()


class FakeCoreAudio:
    """Fabrik für die Attrappen plus die Geräteauflösung, die der Player
    benutzt. `fail` lässt jedes Öffnen scheitern (kein Gerät, kein CoreAudio)."""

    def __init__(self, fail=False, hang=(), devices=(("Kopfhörer", 7),
                                                     ("MacBook Pro-Lautsprecher", 3))):
        self.units = []
        self.opened = []                   # je Öffnung das gewünschte Gerät
        self.fail = fail
        self._hang = tuple(hang)
        self.devices = list(devices)

    def open(self, callback, rate, device):
        self.opened.append(device)
        if self.fail:
            raise OSError("kein Ausgabegerät")
        unit = FakeUnit(callback, rate, device, hang=self._hang)
        self.units.append(unit)
        unit.start()
        return unit

    def find_output_device(self, name):
        for dev_name, dev_id in self.devices:
            if dev_name == name:
                return dev_id
        return None

    def output_devices(self):
        return [(dev_id, name) for name, dev_id in self.devices]

    def release_all(self):
        for u in self.units:
            u.release()


def install(monkeypatch, fake):
    """Attrappe an beide Stellen hängen, an denen der Player die Außenwelt
    berührt."""
    monkeypatch.setattr(beep, "_open_unit", fake.open)
    monkeypatch.setattr(beep.coreaudio, "find_output_device", fake.find_output_device)
    return fake


def wait_until(pred, timeout=3.0):
    """Auf ein Ergebnis des Abspiel-Threads warten, statt eine Dauer zu raten."""
    ende = time.monotonic() + timeout
    while time.monotonic() < ende:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


PAYLOAD_FRAMES = 320         # 20 ms bei 16 kHz, Inhalt ist für den Test egal
PAYLOAD_WERT = 1234


def _wav(path, frames=PAYLOAD_FRAMES, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % frames, *([PAYLOAD_WERT] * frames)))
    return str(path)


def _payload_bytes(frames=PAYLOAD_FRAMES):
    return struct.pack("<%dh" % frames, *([PAYLOAD_WERT] * frames))


@pytest.fixture
def ton(tmp_path):
    return _wav(tmp_path / "start.wav")


@pytest.fixture
def player():
    p = beep._MacPlayer()
    yield p
    p.close()


def _preroll_bytes():
    return int(beep.OUT_RATE * beep.PREROLL_MS / 1000.0) * 2


def _warte_auf_ton(player, fake, erwartet):
    """Wartet, bis der Abspiel-Thread `erwartet` Bytes bereitgelegt hat."""
    return wait_until(lambda: len(fake.units) >= 1
                      and len(player._pending) >= erwartet)


def _pump_alles(unit, player, reserve=2):
    """So viele Blöcke abholen, bis nichts mehr ansteht (plus Reserve)."""
    blocks = (len(player._pending) // (unit.blocksize * 2)) + 1 + reserve
    return unit.pump(blocks)


# ------------------------------------------------------------- Gerätewahl
def test_ohne_einstellung_folgt_die_einheit_dem_systemstandard(player, ton, monkeypatch):
    """Der Fehler aus v2.6.0: die Töne kamen aus den Laptop-Lautsprechern, egal
    was der Nutzer eingestellt hatte. Ohne festes Gerät wird die Einheit ohne
    Gerätebindung geöffnet — dann folgt CoreAudio dem System-Standard, auch
    wenn er sich später ändert."""
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes())

    assert fake.opened == [None]                 # None = dem Standard folgen
    assert fake.units[0].device is None
    assert fake.units[0].rate == beep.OUT_RATE


def test_eingestelltes_geraet_bindet_die_einheit(player, ton, monkeypatch):
    fake = install(monkeypatch, FakeCoreAudio())
    player.set_device("Kopfhörer")

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes())

    assert fake.opened == [7]                    # AudioDeviceID der Kopfhörer
    assert fake.units[0].device == 7


def test_geraetewechsel_oeffnet_die_einheit_neu(player, ton, monkeypatch):
    """Stellt der Nutzer im Kontrollzentrum um, darf der nächste Ton nicht mehr
    auf dem alten Gerät landen."""
    fake = install(monkeypatch, FakeCoreAudio())
    player.set_device("Kopfhörer")

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes())
    _pump_alles(fake.units[0], player)

    player.set_device("MacBook Pro-Lautsprecher")
    player.play(ton)
    assert wait_until(lambda: len(fake.units) == 2)

    assert "close" in fake.units[0].calls
    assert fake.opened == [7, 3]
    assert not player._pending or len(player._pending) >= _preroll_bytes()


def test_zurueck_auf_systemstandard_loest_die_bindung(player, ton, monkeypatch):
    fake = install(monkeypatch, FakeCoreAudio())
    player.set_device("Kopfhörer")
    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes())
    _pump_alles(fake.units[0], player)

    player.set_device("system")
    player.play(ton)
    assert wait_until(lambda: len(fake.units) == 2)
    assert fake.opened == [7, None]


def test_verschwundenes_geraet_faellt_auf_den_standard_zurueck(player, ton, monkeypatch):
    """Ein eingestelltes Gerät, das nicht mehr da ist, darf nicht in Stille
    enden: lieber der falsche Lautsprecher als gar kein Ton. Gemeldet wird es
    genau einmal, nicht bei jedem Ton."""
    fake = install(monkeypatch, FakeCoreAudio())
    zeilen = []
    monkeypatch.setattr(beep.audio, "_log", zeilen.append)
    player.set_device("Gibt Es Nicht")

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes())
    assert fake.opened == [None]
    assert len(zeilen) == 1
    assert "Gibt Es Nicht" in zeilen[0]

    _pump_alles(fake.units[0], player)
    player.play(ton)
    assert wait_until(lambda: len(player._pending) >= len(_payload_bytes()))
    time.sleep(0.05)
    assert len(zeilen) == 1                      # keine zweite Zeile


def test_systemstandard_wechsel_oeffnet_nichts_neu(player, ton, monkeypatch):
    """Gegenprobe zur Gerätebindung: solange „System-Standard" eingestellt ist,
    folgt die Einheit dem Wechsel selbst. Der Player darf sie deswegen NICHT
    schließen — sonst ginge die Warmhaltung bei jedem Wechsel verloren."""
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes())
    _pump_alles(fake.units[0], player)

    # Der System-Standard wandert von den Kopfhörern zu den Lautsprechern.
    fake.devices = [("MacBook Pro-Lautsprecher", 3)]
    player.play(ton)
    assert wait_until(lambda: len(player._pending) >= len(_payload_bytes()))

    assert len(fake.units) == 1                  # dieselbe Einheit
    assert fake.opened == [None]


# -------------------------------------------------------------- kalt / warm
def test_kalter_ton_oeffnet_einheit_und_schickt_vorlauf(player, ton, monkeypatch):
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes() + len(_payload_bytes()))

    unit = fake.units[0]
    assert unit.calls[0] == "start"

    aus = _pump_alles(unit, player)
    grenze = _preroll_bytes()
    assert set(aus[:grenze]) == {0}              # 250 ms Stille
    assert aus[grenze:grenze + len(_payload_bytes())] == _payload_bytes()


def test_ton_wird_ueber_mehrere_callbacks_zusammengesetzt(player, ton, monkeypatch):
    """Ein Ton ist länger als ein Callback-Block: er muss trotzdem lückenlos
    und vollständig im Ausgang landen."""
    monkeypatch.setattr(beep, "PREROLL_MS", 0)   # hier zählt nur der Ton selbst
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, len(_payload_bytes()))

    unit = fake.units[0]
    assert len(_payload_bytes()) > unit.blocksize * 2   # braucht mehrere Blöcke
    aus = b""
    for _ in range(4):                           # blockweise abholen wie ein Gerät
        aus = unit.pump(1)
    assert aus[:len(_payload_bytes())] == _payload_bytes()
    assert not player._pending                   # nichts liegen geblieben


def test_callback_fuellt_ohne_ton_mit_stille(player, ton, monkeypatch):
    """Nach dem Ton läuft die Einheit weiter — der Callback liefert Nullen statt
    nichts, sonst entsteht genau der Unterlauf, der als 'Pfft' zu hören war."""
    monkeypatch.setattr(beep, "PREROLL_MS", 0)
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, len(_payload_bytes()))
    unit = fake.units[0]
    _pump_alles(unit, player)

    vorher = len(unit.out)
    unit.pump(3)                                 # nichts steht mehr an
    stille = unit.out[vorher:]
    assert len(stille) == 3 * unit.blocksize * 2
    assert set(stille) == {0}


def test_warmer_ton_ohne_neue_einheit_und_ohne_vorlauf(player, ton, monkeypatch):
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes() + len(_payload_bytes()))
    unit = fake.units[0]
    _pump_alles(unit, player)

    player.play(ton)
    assert wait_until(lambda: len(player._pending) >= len(_payload_bytes()))

    assert len(fake.units) == 1                  # Einheit blieb offen
    vorher = len(unit.out)
    _pump_alles(unit, player)
    assert unit.out[vorher:vorher + len(_payload_bytes())] == _payload_bytes()
    assert "close" not in unit.calls


def test_nach_warm_keep_schliesst_einheit_naechster_ton_hat_vorlauf(
        player, ton, monkeypatch):
    monkeypatch.setattr(beep, "WARM_KEEP", 0.05)
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes() + len(_payload_bytes()))
    assert wait_until(lambda: "close" in fake.units[0].calls)
    assert not player._pending                   # Reste der alten Einheit weg

    player.play(ton)
    assert wait_until(lambda: len(fake.units) == 2
                      and len(player._pending) >= _preroll_bytes())
    aus = _pump_alles(fake.units[1], player)
    assert set(aus[:_preroll_bytes()]) == {0}    # wieder kalt


def test_tote_einheit_wird_ersetzt(player, ton, monkeypatch):
    """Fällt das Gerät weg, läuft die Einheit nicht mehr — der nächste Ton darf
    nicht in einen Puffer wandern, den kein Callback mehr leert."""
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes() + len(_payload_bytes()))
    _pump_alles(fake.units[0], player)
    fake.units[0].active = False                 # Gerät weg

    player.play(ton)
    assert wait_until(lambda: len(fake.units) == 2)
    assert "close" in fake.units[0].calls


def test_stumm_gewordene_einheit_wird_ersetzt(player, ton, monkeypatch):
    """Die Einheit meldet sich als aktiv, hat aber seit dem Öffnen nie einen
    Block abgeholt — ein Gerät, das schon beim Öffnen nicht mehr da war. Nach
    DEAD_AFTER gilt sie als tot."""
    monkeypatch.setattr(beep, "DEAD_AFTER", 0.05)
    zeilen = []
    monkeypatch.setattr(beep.audio, "_log", zeilen.append)
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    assert _warte_auf_ton(player, fake, _preroll_bytes())
    # kein pump(): die Einheit hat nie einen Block abgeholt
    time.sleep(0.1)

    player.play(ton)
    assert wait_until(lambda: len(fake.units) == 2)
    assert any("liefert nichts mehr" in z for z in zeilen)


def test_einheit_die_aufhoert_zu_liefern_wird_ersetzt(player, ton, monkeypatch):
    """Der Fall, den ein Review gefunden hat: die Kopfhörer werden getrennt,
    während die Einheit an sie gebunden ist. Sie meldet sich weiter als aktiv
    und hat schon hunderte Blöcke abgeholt — nur eben keinen mehr seit dem
    letzten Ton. Ein Lebendigkeitszeichen, das nur „schon einmal gerendert"
    prüft, hielte sie für gesund, und jeder weitere Ton verschwände bis zum
    Ablauf von WARM_KEEP wortlos in `_pending`.

    Geprüft wird der Ablauf, den der Player wirklich hat: der erste Ton läuft
    mit arbeitender Einheit, danach fällt das Gerät weg. Der nächste Ton geht
    verloren — zu diesem Zeitpunkt gab es seit dem letzten Ton Fortschritt, die
    Einheit gilt zu Recht als gesund. Erst der übernächste sieht den
    stehengebliebenen Zähler und öffnet neu. Ein Ton Verlust, nicht eine
    Minute."""
    monkeypatch.setattr(beep, "DEAD_AFTER", 0.05)
    zeilen = []
    monkeypatch.setattr(beep.audio, "_log", zeilen.append)
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)                              # Ton 1: Einheit arbeitet
    assert _warte_auf_ton(player, fake, _preroll_bytes())
    _pump_alles(fake.units[0], player)
    assert player._rendered > 0

    player.play(ton)                              # Ton 2: Gerät ist weg, kein pump
    assert wait_until(lambda: len(player._pending) >= len(_payload_bytes()))
    assert len(fake.units) == 1                   # noch für gesund gehalten
    time.sleep(0.1)

    player.play(ton)                              # Ton 3: Zähler steht -> tot
    assert wait_until(lambda: len(fake.units) == 2)
    assert "close" in fake.units[0].calls
    assert any("liefert nichts mehr" in z for z in zeilen)
    assert fake.units[1].device is None           # neue Einheit, aktueller Standard


def test_zwei_toene_kurz_hintereinander_reissen_die_einheit_nicht_mit(
        player, ton, monkeypatch):
    """Gegenprobe zu DEAD_AFTER: zwischen Start- und Stoppton kann die Einheit
    noch keinen Block abgeholt haben. Sie darf deswegen nicht für tot erklärt
    und geschlossen werden — das würde den ersten Ton mitnehmen."""
    fake = install(monkeypatch, FakeCoreAudio())

    player.play(ton)
    player.play(ton)
    assert wait_until(lambda: len(player._pending)
                      >= _preroll_bytes() + 2 * len(_payload_bytes()))
    assert len(fake.units) == 1


# ------------------------------------------------------------ Aufrufer frei
def test_start_und_stop_kehren_sofort_zurueck(tmp_path, monkeypatch):
    """Auch wenn die Attrappe im Öffnen hängt: der Hotkey-Thread wartet nie."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(beep, "START_WAV", _wav(tmp_path / "s.wav"))
    monkeypatch.setattr(beep, "STOP_WAV", _wav(tmp_path / "e.wav"))
    fake = install(monkeypatch, FakeCoreAudio(hang=("start",)))
    p = beep._MacPlayer()
    monkeypatch.setattr(beep, "_PLAYER", p)
    try:
        t0 = time.monotonic()
        beep.start()
        beep.stop()
        dauer = time.monotonic() - t0
        assert dauer < 0.5                       # großzügig, echt ist es Mikrosekunden
        assert wait_until(lambda: bool(fake.units))
    finally:
        fake.release_all()
        p.close()


def test_haengendes_schliessen_gibt_abspiel_thread_frei(player, ton, monkeypatch):
    """Eine Einheit, deren close() nie zurückkehrt, wird nach der Frist
    aufgegeben — der nächste Ton läuft über eine neue."""
    monkeypatch.setattr(beep, "WARM_KEEP", 0.05)
    monkeypatch.setattr(beep, "STOP_TIMEOUT", 0.2)
    fake = install(monkeypatch, FakeCoreAudio(hang=("close",)))
    try:
        player.play(ton)
        assert _warte_auf_ton(player, fake, _preroll_bytes() + len(_payload_bytes()))
        assert wait_until(lambda: "close" in fake.units[0].calls)

        player.play(ton)
        assert wait_until(lambda: len(fake.units) == 2
                          and len(player._pending) >= _preroll_bytes())
    finally:
        fake.release_all()


# ----------------------------------------------------------- Rückfall afplay
def test_ohne_ausgabegeraet_geht_der_ton_ueber_afplay(player, ton, monkeypatch):
    """Kein Gerät, kein CoreAudio: der Ton geht über afplay, und afplay startet
    einen eigenen Prozess — der trifft ebenfalls das aktuelle Standardgerät."""
    zeilen = []
    monkeypatch.setattr(beep.audio, "_log", zeilen.append)
    install(monkeypatch, FakeCoreAudio(fail=True))
    rufe = []
    monkeypatch.setattr(beep.subprocess, "Popen",
                        lambda args, **kw: rufe.append(args))

    player.play(ton)
    assert wait_until(lambda: bool(rufe))
    assert rufe[0][:2] == ["afplay", ton]
    assert any("nicht zu öffnen" in z for z in zeilen)


def test_fremdes_wav_format_geht_ueber_afplay(player, tmp_path, monkeypatch):
    """Stereo oder 24 Bit nimmt der eigene Weg nicht an."""
    install(monkeypatch, FakeCoreAudio())
    pfad = str(tmp_path / "stereo.wav")
    with wave.open(pfad, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 2 * 100)
    rufe = []
    monkeypatch.setattr(beep.subprocess, "Popen",
                        lambda args, **kw: rufe.append(args))

    player.play(pfad)
    assert wait_until(lambda: bool(rufe))
    assert rufe[0][:2] == ["afplay", pfad]


def test_abweichende_rate_wird_auf_out_rate_gerechnet(player, tmp_path, monkeypatch):
    """Die Einheit läuft immer auf OUT_RATE; ein WAV mit anderer Rate wird
    umgerechnet, statt mit falscher Tonhöhe zu spielen."""
    fake = install(monkeypatch, FakeCoreAudio())
    pfad = _wav(tmp_path / "8k.wav", rate=8000)

    player.play(pfad)
    # halbe Quellrate -> rund doppelt so viele Abtastwerte wie im Original
    erwartet = _preroll_bytes() + 1.5 * len(_payload_bytes())
    assert _warte_auf_ton(player, fake, erwartet)

    assert fake.units[0].rate == beep.OUT_RATE
    aus = _pump_alles(fake.units[0], player)
    ton_bytes = len(aus[_preroll_bytes():].rstrip(b"\x00"))
    assert 1.5 * len(_payload_bytes()) < ton_bytes < 2.5 * len(_payload_bytes())


def test_fehlende_datei_erzeugt_nichts(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    gespielt = []
    monkeypatch.setattr(beep, "_player", lambda: gespielt.append("player"))
    beep._play(str(tmp_path / "gibtsnicht.wav"))
    assert gespielt == []


# --------------------------------------------------- Schnittstelle nach außen
def test_list_outputs_kommt_aus_coreaudio(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(beep.coreaudio, "output_devices",
                        lambda: [(3, "MacBook Pro-Lautsprecher"), (7, "Kopfhörer")])
    assert beep.list_outputs() == [("MacBook Pro-Lautsprecher",
                                    "MacBook Pro-Lautsprecher"),
                                   ("Kopfhörer", "Kopfhörer")]


def test_list_outputs_ist_ausserhalb_von_macos_leer(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert beep.list_outputs() == []


def test_set_output_reicht_den_namen_durch(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    p = beep._MacPlayer()
    monkeypatch.setattr(beep, "_PLAYER", p)
    beep.set_output("Kopfhörer")
    assert p._device == "Kopfhörer"
    beep.set_output("")                          # leer = Standard folgen
    assert p._device == "system"


def test_set_output_ist_ausserhalb_von_macos_wirkungslos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(beep, "_player",
                        lambda: pytest.fail("kein Player außerhalb von macOS"))
    beep.set_output("Kopfhörer")


# ------------------------------------------------------ Verdrahtung im Daemon
def test_daemon_setzt_das_geraet_vor_dem_startton(monkeypatch):
    """Die Einstellung nützt nichts, wenn sie den Abspielweg nie erreicht. Der
    Daemon lädt die Config vor jedem Diktat neu und reicht die Gerätewahl
    weiter, BEVOR der Startton kommt — sonst griffe eine Änderung im
    Kontrollzentrum erst nach einem Neustart."""
    from quassel import daemon as daemon_mod

    gesetzt = []
    monkeypatch.setattr(daemon_mod.beep, "set_output", gesetzt.append)
    monkeypatch.setattr(daemon_mod, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(daemon_mod, "mac_backend", lambda: "sounddevice")

    class Cfg:
        ui_language = "auto"
        beep_output = "Kopfhörer"
        mic = "default"

        def reload(self):
            return False

    class Rec:
        def start(self, _mic):
            return False                         # bricht direkt nach dem Setzen ab

    d = object.__new__(daemon_mod.Daemon)
    d.cfg, d.rec = Cfg(), Rec()

    assert d.start_recording() is False
    assert gesetzt == ["Kopfhörer"]


# ------------------------------------------------- Linux/Windows unverändert
def test_linux_bleibt_beim_subprozess_player(ton, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(beep.os, "name", "posix")
    monkeypatch.setattr(beep.shutil, "which", lambda name: name == "pw-play")
    rufe = []
    monkeypatch.setattr(beep.subprocess, "Popen",
                        lambda args, **kw: rufe.append(args))

    beep._play(ton)
    assert rufe == [["pw-play", ton]]
