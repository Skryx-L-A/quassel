"""Tests des macOS-Abspielwegs (beep._MacPlayer) ohne PortAudio.

Kein Test öffnet ein echtes Gerät und keiner erzeugt hörbaren Ton: gespielt
wird ausschließlich gegen die Stream-Attrappe, die WAV-Dateien liegen unter
tmp_path. Den Audio-Thread spielt der Test selbst nach — pump() ruft den
Callback so oft auf, wie ein laufendes Gerät es täte. Threads, Queue und
Fristen sind echt.
"""
import os
import struct
import sys
import threading
import time
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import beep


# ------------------------------------------------------------------ Doubles
class FakeStatus:
    """CallbackFlags-Ersatz: falsch, solange keine Meldung anliegt."""

    def __init__(self, output_underflow=False):
        self.output_underflow = output_underflow

    def __bool__(self):
        return bool(self.output_underflow)


class FakeOutStream:
    """sounddevice-Ausgabestrom-Double. Die in `hang` genannten Aufrufe kehren
    nicht zurück, bis release() sie freigibt — so wie ein Bluetooth-Gerät, das
    nach dem Wegfallen der Verbindung nicht mehr antwortet."""

    def __init__(self, callback=None, blocksize=None, latency=None, hang=()):
        self.calls = []
        self.callback = callback
        self.blocksize = blocksize or 512     # ohne Vorgabe wählt PortAudio selbst
        self.latency = latency
        self.out = bytearray()                # alles, was der Callback ausgab
        self.active = False
        self._hang = set(hang)
        self._block = threading.Event()

    def start(self):
        self.calls.append("start")
        self.active = True
        if "start" in self._hang:
            self._block.wait(30)

    def pump(self, blocks=1, underflow=False):
        """Den Audio-Thread nachspielen: `blocks` Blöcke abholen lassen."""
        for _ in range(blocks):
            buf = bytearray(self.blocksize * 2)      # int16-Mono
            self.callback(buf, self.blocksize, None, FakeStatus(underflow))
            self.out.extend(buf)
        return bytes(self.out)

    def abort(self):
        self._halt("abort")

    def stop(self):
        self._halt("stop")

    def close(self):
        self._halt("close")

    def _halt(self, name):
        self.calls.append(name)
        self.active = False
        if name in self._hang:
            self._block.wait(30)

    def release(self):
        self._block.set()


class FakeDefaults:
    device = (0, 1)          # (Eingabe, Ausgabe)


class FakeSD:
    """sounddevice-Ersatz: merkt sich jede geöffnete Rate und gibt eine
    Attrappe zurück. Raten aus `reject` werden abgelehnt wie von einem Gerät,
    das sie nicht kann; `reject_blocksize` lehnt nur den Versuch mit kleinen
    Blöcken und niedriger Latenz ab."""

    def __init__(self, reject=(), dev_rate=48000, hang=(),
                 reject_blocksize=False):
        self.opened = []
        self.streams = []
        self.default = FakeDefaults()
        self._reject = set(reject)
        self._dev_rate = dev_rate
        self._hang = tuple(hang)
        self._reject_blocksize = reject_blocksize

    def RawOutputStream(self, samplerate=None, callback=None, blocksize=None,
                        latency=None, **_kw):
        self.opened.append(samplerate)
        if samplerate in self._reject:
            raise RuntimeError("Rate wird vom Geraet nicht unterstuetzt")
        if self._reject_blocksize and blocksize is not None:
            raise RuntimeError("Blockgroesse wird nicht unterstuetzt")
        stream = FakeOutStream(callback=callback, blocksize=blocksize,
                               latency=latency, hang=self._hang)
        self.streams.append(stream)
        return stream

    def query_devices(self, _device):
        return {"default_samplerate": self._dev_rate}

    def release_all(self):
        for s in self.streams:
            s.release()


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


def _preroll_bytes(rate=beep.OUT_RATE):
    return int(rate * beep.PREROLL_MS / 1000.0) * 2


def _warte_auf_ton(player, sd, erwartet):
    """Wartet, bis der Abspiel-Thread `erwartet` Bytes bereitgelegt hat."""
    return wait_until(lambda: len(sd.streams) >= 1
                      and len(player._pending) >= erwartet)


def _pump_alles(stream, player, reserve=2):
    """So viele Blöcke abholen, bis nichts mehr ansteht (plus Reserve)."""
    blocks = (len(player._pending) // (stream.blocksize * 2)) + 1 + reserve
    return stream.pump(blocks)


# -------------------------------------------------------------- kalt / warm
def test_kalter_ton_oeffnet_strom_und_schickt_vorlauf(player, ton, monkeypatch):
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, _preroll_bytes() + len(_payload_bytes()))

    assert sd.opened == [16000]                  # sd16 wie im Aufnahmepfad
    stream = sd.streams[0]
    assert stream.calls[0] == "start"
    assert stream.blocksize == beep.BLOCKSIZE    # kleiner Puffer, keine PortAudio-Vorgabe
    assert stream.latency == "low"

    aus = _pump_alles(stream, player)
    grenze = _preroll_bytes()
    assert set(aus[:grenze]) == {0}              # 250 ms Stille
    assert aus[grenze:grenze + len(_payload_bytes())] == _payload_bytes()


def test_ton_wird_ueber_mehrere_callbacks_zusammengesetzt(player, ton, monkeypatch):
    """Ein Ton ist länger als ein Callback-Block: er muss trotzdem lückenlos
    und vollständig im Ausgang landen."""
    monkeypatch.setattr(beep, "PREROLL_MS", 0)   # hier zählt nur der Ton selbst
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, len(_payload_bytes()))

    stream = sd.streams[0]
    assert len(_payload_bytes()) > stream.blocksize * 2   # braucht mehrere Blöcke
    aus = b""
    for _ in range(4):                           # blockweise abholen wie ein Gerät
        aus = stream.pump(1)
    assert aus[:len(_payload_bytes())] == _payload_bytes()
    assert not player._pending                   # nichts liegen geblieben


def test_callback_fuellt_ohne_ton_mit_stille(player, ton, monkeypatch):
    """Nach dem Ton läuft der Strom weiter — der Callback liefert Nullen statt
    nichts, sonst entsteht genau der Unterlauf, der als 'Pfft' zu hören war."""
    monkeypatch.setattr(beep, "PREROLL_MS", 0)
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, len(_payload_bytes()))
    stream = sd.streams[0]
    _pump_alles(stream, player)

    vorher = len(stream.out)
    stream.pump(3)                               # nichts steht mehr an
    stille = stream.out[vorher:]
    assert len(stille) == 3 * stream.blocksize * 2
    assert set(stille) == {0}


def test_warmer_ton_ohne_neuen_strom_und_ohne_vorlauf(player, ton, monkeypatch):
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, _preroll_bytes() + len(_payload_bytes()))
    stream = sd.streams[0]
    _pump_alles(stream, player)

    player.play(ton)
    assert wait_until(lambda: len(player._pending) >= len(_payload_bytes()))

    assert len(sd.streams) == 1                  # Strom blieb offen
    vorher = len(stream.out)
    _pump_alles(stream, player)
    assert stream.out[vorher:vorher + len(_payload_bytes())] == _payload_bytes()
    assert "abort" not in stream.calls


def test_nach_warm_keep_schliesst_strom_naechster_ton_hat_vorlauf(
        player, ton, monkeypatch):
    monkeypatch.setattr(beep, "WARM_KEEP", 0.05)
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, _preroll_bytes() + len(_payload_bytes()))
    assert wait_until(lambda: "abort" in sd.streams[0].calls)
    assert "close" in sd.streams[0].calls
    assert not player._pending                   # Reste des alten Stroms weg

    player.play(ton)
    assert wait_until(lambda: len(sd.streams) == 2
                      and len(player._pending) >= _preroll_bytes())
    aus = _pump_alles(sd.streams[1], player)
    assert set(aus[:_preroll_bytes()]) == {0}    # wieder kalt


def test_toter_strom_wird_ersetzt(player, ton, monkeypatch):
    """Fällt das Gerät weg, läuft der Strom nicht mehr — der nächste Ton darf
    nicht in einen Puffer wandern, den kein Callback mehr leert."""
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, _preroll_bytes() + len(_payload_bytes()))
    _pump_alles(sd.streams[0], player)
    sd.streams[0].active = False                 # Gerät weg

    player.play(ton)
    assert wait_until(lambda: len(sd.streams) == 2)
    assert "abort" in sd.streams[0].calls


# ------------------------------------------------------------ Unterlauf-Hinweis
def test_unterlauf_wird_genau_einmal_je_strom_gemeldet(player, ton, monkeypatch):
    """Der Callback setzt nur einen Merker; geschrieben wird im Abspiel-Thread,
    beim nächsten Ton oder spätestens beim Schließen (siehe Test darunter)."""
    monkeypatch.setattr(beep, "PREROLL_MS", 0)
    zeilen = []
    monkeypatch.setattr(beep.audio, "_log", zeilen.append)
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, len(_payload_bytes()))
    sd.streams[0].pump(2, underflow=True)
    sd.streams[0].pump(2, underflow=True)        # zweimal gemeldet

    player.play(ton)                             # weiterer Ton, gleicher Strom
    assert wait_until(lambda: len(zeilen) == 1)
    assert "underflow" in zeilen[0]

    sd.streams[0].pump(3, underflow=True)        # leert `_pending` wieder
    player.play(ton)
    assert wait_until(lambda: len(player._pending) >= len(_payload_bytes()))
    time.sleep(0.05)
    assert len(zeilen) == 1                      # keine zweite Zeile je Strom

    sd.streams[0].active = False                 # neuer Strom -> neue Zählung
    player.play(ton)
    assert wait_until(lambda: len(sd.streams) == 2)
    sd.streams[1].pump(2, underflow=True)
    player.play(ton)
    assert wait_until(lambda: len(zeilen) == 2)


def test_unterlauf_wird_spaetestens_beim_schliessen_gemeldet(player, ton, monkeypatch):
    """Kommt kein weiterer Ton, schreibt der Abspiel-Thread die Zeile, wenn er
    den Strom nach WARM_KEEP schließt — die Meldung geht nie verloren."""
    monkeypatch.setattr(beep, "PREROLL_MS", 0)
    monkeypatch.setattr(beep, "WARM_KEEP", 0.5)
    zeilen = []
    monkeypatch.setattr(beep.audio, "_log", zeilen.append)
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, len(_payload_bytes()))
    sd.streams[0].pump(2, underflow=True)

    assert wait_until(lambda: "abort" in sd.streams[0].calls)
    assert len(zeilen) == 1
    assert "underflow" in zeilen[0]


def test_ohne_unterlauf_keine_zeile(player, ton, monkeypatch):
    zeilen = []
    monkeypatch.setattr(beep.audio, "_log", zeilen.append)
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, _preroll_bytes() + len(_payload_bytes()))
    _pump_alles(sd.streams[0], player)
    player.close()
    assert zeilen == []


# ------------------------------------------------------------ Aufrufer frei
def test_start_und_stop_kehren_sofort_zurueck(tmp_path, monkeypatch):
    """Auch wenn die Attrappe im Öffnen hängt: der Hotkey-Thread wartet nie."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(beep, "START_WAV", _wav(tmp_path / "s.wav"))
    monkeypatch.setattr(beep, "STOP_WAV", _wav(tmp_path / "e.wav"))
    sd = FakeSD(hang=("start",))
    monkeypatch.setattr(beep, "_sd", lambda: sd)
    p = beep._MacPlayer()
    monkeypatch.setattr(beep, "_PLAYER", p)
    try:
        t0 = time.monotonic()
        beep.start()
        beep.stop()
        dauer = time.monotonic() - t0
        assert dauer < 0.5                       # großzügig, echt ist es Mikrosekunden
        assert wait_until(lambda: bool(sd.streams))
    finally:
        sd.release_all()
        p.close()


def test_haengendes_abort_gibt_abspiel_thread_frei(player, ton, monkeypatch):
    """Ein Strom, dessen abort()/close() nie zurückkehrt, wird nach der Frist
    aufgegeben — der nächste Ton läuft über einen neuen Strom."""
    monkeypatch.setattr(beep, "WARM_KEEP", 0.05)
    monkeypatch.setattr(beep, "STOP_TIMEOUT", 0.2)
    sd = FakeSD(hang=("abort", "close"))
    monkeypatch.setattr(beep, "_sd", lambda: sd)
    try:
        player.play(ton)
        assert _warte_auf_ton(player, sd, _preroll_bytes() + len(_payload_bytes()))
        assert wait_until(lambda: "abort" in sd.streams[0].calls)

        player.play(ton)
        assert wait_until(lambda: len(sd.streams) == 2
                          and len(player._pending) >= _preroll_bytes())
    finally:
        sd.release_all()


# ----------------------------------------------------------- Rückfall/Raten
def test_ohne_sounddevice_geht_der_ton_ueber_afplay(player, ton, monkeypatch):
    monkeypatch.setattr(beep, "_sd", lambda: None)
    rufe = []
    monkeypatch.setattr(beep.subprocess, "Popen",
                        lambda args, **kw: rufe.append(args))

    player.play(ton)
    assert wait_until(lambda: bool(rufe))
    assert rufe[0][:2] == ["afplay", ton]


def test_scheiterndes_oeffnen_geht_ueber_afplay(player, ton, monkeypatch):
    sd = FakeSD(reject=(16000, 48000))
    monkeypatch.setattr(beep, "_sd", lambda: sd)
    rufe = []
    monkeypatch.setattr(beep.subprocess, "Popen",
                        lambda args, **kw: rufe.append(args))

    player.play(ton)
    assert wait_until(lambda: bool(rufe))
    # je Rate zwei Versuche: mit und ohne Blockgrößen-Vorgabe
    assert sd.opened == [16000, 16000, 48000, 48000]
    assert rufe[0][:2] == ["afplay", ton]


def test_ohne_kleine_bloecke_oeffnet_er_ohne_vorgabe(player, ton, monkeypatch):
    """Nimmt das Gerät blocksize/latency nicht an, wird derselbe Strom ohne
    diese Argumente geöffnet statt auf afplay auszuweichen."""
    sd = FakeSD(reject_blocksize=True)
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert _warte_auf_ton(player, sd, _preroll_bytes() + len(_payload_bytes()))

    assert sd.opened == [16000, 16000]
    assert sd.streams[0].blocksize == 512        # Attrappen-Vorgabe: keine gesetzt
    assert sd.streams[0].latency is None
    aus = _pump_alles(sd.streams[0], player)
    assert set(aus[:_preroll_bytes()]) == {0}


def test_ohne_16k_wird_auf_geraeterate_hochgerechnet(player, ton, monkeypatch):
    sd = FakeSD(reject=(16000,), dev_rate=48000)
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    erwartet = _preroll_bytes(48000) + 2.5 * len(_payload_bytes())
    player.play(ton)
    assert _warte_auf_ton(player, sd, erwartet)

    assert sd.opened == [16000, 16000, 48000]
    aus = _pump_alles(sd.streams[0], player)
    assert set(aus[:_preroll_bytes(48000)]) == {0}
    # dreifache Rate -> rund dreimal so viele Abtastwerte (Filtervorlauf fehlt)
    ton_bytes = len(aus[_preroll_bytes(48000):].rstrip(b"\x00"))
    assert 2.5 * len(_payload_bytes()) < ton_bytes < 3.5 * len(_payload_bytes())


def test_fehlende_datei_erzeugt_nichts(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    gespielt = []
    monkeypatch.setattr(beep, "_player", lambda: gespielt.append("player"))
    beep._play(str(tmp_path / "gibtsnicht.wav"))
    assert gespielt == []


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
