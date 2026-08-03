"""Tests des macOS-Abspielwegs (beep._MacPlayer) ohne PortAudio.

Kein Test öffnet ein echtes Gerät und keiner erzeugt hörbaren Ton: gespielt
wird ausschließlich gegen die Stream-Attrappe, die WAV-Dateien liegen unter
tmp_path. Threads, Queue und Fristen sind echt.
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
class FakeOutStream:
    """sounddevice-Ausgabestrom-Double. Die in `hang` genannten Aufrufe kehren
    nicht zurück, bis release() sie freigibt — so wie ein Bluetooth-Gerät, das
    nach dem Wegfallen der Verbindung nicht mehr antwortet."""

    def __init__(self, hang=()):
        self.calls = []
        self.writes = []
        self._hang = set(hang)
        self._block = threading.Event()

    def start(self):
        self.calls.append("start")

    def write(self, data):
        self.writes.append(bytes(data))
        if "write" in self._hang:
            self._block.wait(30)

    def abort(self):
        self._halt("abort")

    def stop(self):
        self._halt("stop")

    def close(self):
        self._halt("close")

    def _halt(self, name):
        self.calls.append(name)
        if name in self._hang:
            self._block.wait(30)

    def release(self):
        self._block.set()


class FakeDefaults:
    device = (0, 1)          # (Eingabe, Ausgabe)


class FakeSD:
    """sounddevice-Ersatz: merkt sich jede geöffnete Rate und gibt eine
    Attrappe zurück. Raten aus `reject` werden abgelehnt wie von einem Gerät,
    das sie nicht kann."""

    def __init__(self, reject=(), dev_rate=48000, hang=()):
        self.opened = []
        self.streams = []
        self.default = FakeDefaults()
        self._reject = set(reject)
        self._dev_rate = dev_rate
        self._hang = tuple(hang)

    def RawOutputStream(self, samplerate=None, **_kw):
        self.opened.append(samplerate)
        if samplerate in self._reject:
            raise RuntimeError("Rate wird vom Geraet nicht unterstuetzt")
        stream = FakeOutStream(hang=self._hang)
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


# -------------------------------------------------------------- kalt / warm
def test_kalter_ton_oeffnet_strom_und_schickt_vorlauf(player, ton, monkeypatch):
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert wait_until(lambda: len(sd.streams) == 1 and len(sd.streams[0].writes) == 2)

    assert sd.opened == [16000]                  # sd16 wie im Aufnahmepfad
    stream = sd.streams[0]
    assert stream.calls[0] == "start"
    vorlauf, nutzdaten = stream.writes
    assert len(vorlauf) == _preroll_bytes()      # 350 ms Stille
    assert set(vorlauf) == {0}
    assert nutzdaten == _payload_bytes()


def test_warmer_ton_ohne_neuen_strom_und_ohne_vorlauf(player, ton, monkeypatch):
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert wait_until(lambda: len(sd.streams) == 1 and len(sd.streams[0].writes) == 2)
    player.play(ton)
    assert wait_until(lambda: len(sd.streams[0].writes) == 3)

    assert len(sd.streams) == 1                  # Strom blieb offen
    assert sd.streams[0].writes[2] == _payload_bytes()   # direkt der Ton
    assert "abort" not in sd.streams[0].calls


def test_nach_warm_keep_schliesst_strom_naechster_ton_hat_vorlauf(
        player, ton, monkeypatch):
    monkeypatch.setattr(beep, "WARM_KEEP", 0.05)
    sd = FakeSD()
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert wait_until(lambda: len(sd.streams) == 1 and len(sd.streams[0].writes) == 2)
    assert wait_until(lambda: "abort" in sd.streams[0].calls)
    assert "close" in sd.streams[0].calls

    player.play(ton)
    assert wait_until(lambda: len(sd.streams) == 2 and len(sd.streams[1].writes) == 2)
    assert len(sd.streams[1].writes[0]) == _preroll_bytes()   # wieder kalt


# ------------------------------------------------------------ Aufrufer frei
def test_start_und_stop_kehren_sofort_zurueck(tmp_path, monkeypatch):
    """Auch wenn die Attrappe im Schreiben hängt: der Hotkey-Thread wartet nie."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(beep, "START_WAV", _wav(tmp_path / "s.wav"))
    monkeypatch.setattr(beep, "STOP_WAV", _wav(tmp_path / "e.wav"))
    sd = FakeSD(hang=("write",))
    monkeypatch.setattr(beep, "_sd", lambda: sd)
    p = beep._MacPlayer()
    monkeypatch.setattr(beep, "_PLAYER", p)
    try:
        t0 = time.monotonic()
        beep.start()
        beep.stop()
        dauer = time.monotonic() - t0
        assert dauer < 0.5                       # großzügig, echt ist es Mikrosekunden
        assert wait_until(lambda: sd.streams and sd.streams[0].writes)
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
        assert wait_until(lambda: len(sd.streams) == 1 and len(sd.streams[0].writes) == 2)
        assert wait_until(lambda: "abort" in sd.streams[0].calls)

        player.play(ton)
        assert wait_until(lambda: len(sd.streams) == 2 and len(sd.streams[1].writes) == 2)
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
    assert sd.opened == [16000, 48000]           # beide Raten versucht
    assert rufe[0][:2] == ["afplay", ton]


def test_ohne_16k_wird_auf_geraeterate_hochgerechnet(player, ton, monkeypatch):
    sd = FakeSD(reject=(16000,), dev_rate=48000)
    monkeypatch.setattr(beep, "_sd", lambda: sd)

    player.play(ton)
    assert wait_until(lambda: len(sd.streams) == 1 and len(sd.streams[0].writes) == 2)

    assert sd.opened == [16000, 48000]
    vorlauf, nutzdaten = sd.streams[0].writes
    assert len(vorlauf) == _preroll_bytes(48000)
    # dreifache Rate -> rund dreimal so viele Abtastwerte (Filtervorlauf fehlt)
    assert 2.5 * len(_payload_bytes()) < len(nutzdaten) < 3.5 * len(_payload_bytes())


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
