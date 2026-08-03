"""dictionary_words() cacht die Datei über mtime+size — ein Teiltranskript
liest sie nicht mehr bei jedem Aufruf neu von der Platte. dictionary_save()
verwirft den Cache trotzdem explizit (siehe Kommentar in config.py)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import config


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "DICTIONARY", str(tmp_path / "dictionary.txt"))
    config._dictionary_cache["key"] = None
    config._dictionary_cache["words"] = []


def test_missing_dictionary_returns_empty_list(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert config.dictionary_words() == []


def test_reads_words_from_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with open(config.DICTIONARY, "w", encoding="utf-8") as f:
        f.write("PyTorch\nNASA\n\n")
    assert config.dictionary_words() == ["PyTorch", "NASA"]


def test_second_call_does_not_reread_the_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with open(config.DICTIONARY, "w", encoding="utf-8") as f:
        f.write("PyTorch\n")
    assert config.dictionary_words() == ["PyTorch"]

    opens = []
    real_open = open

    def counting_open(path, *a, **kw):
        if path == config.DICTIONARY:
            opens.append(1)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", counting_open)
    assert config.dictionary_words() == ["PyTorch"]
    assert opens == []                          # aus dem Cache, keine Datei geöffnet


def test_changed_mtime_or_size_invalidates_the_cache(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with open(config.DICTIONARY, "w", encoding="utf-8") as f:
        f.write("PyTorch\n")
    assert config.dictionary_words() == ["PyTorch"]
    time.sleep(0.01)
    with open(config.DICTIONARY, "w", encoding="utf-8") as f:
        f.write("PyTorch\nKubernetes\n")
    assert config.dictionary_words() == ["PyTorch", "Kubernetes"]


def test_dictionary_save_invalidates_the_cache(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with open(config.DICTIONARY, "w", encoding="utf-8") as f:
        f.write("PyTorch\n")
    assert config.dictionary_words() == ["PyTorch"]
    config.dictionary_save("PyTorch\nKubernetes")
    assert config.dictionary_words() == ["PyTorch", "Kubernetes"]


def test_mutating_the_returned_list_does_not_corrupt_the_cache(tmp_path, monkeypatch):
    """dictionary_words() gibt eine Kopie zurück -- ein Aufrufer, der die
    Liste verändert, darf den Cache nicht für alle anderen Leser verfälschen."""
    _isolate(tmp_path, monkeypatch)
    with open(config.DICTIONARY, "w", encoding="utf-8") as f:
        f.write("PyTorch\nNASA\n")
    words = config.dictionary_words()
    words.append("Kubernetes")
    words.sort()
    assert config.dictionary_words() == ["PyTorch", "NASA"]   # Cache unangetastet


# ------------------------------------------------------------------ server.env
def _isolate_serverenv(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "SERVERENV", str(tmp_path / "server.env"))


def test_serverenv_round_trip(tmp_path, monkeypatch):
    """Werte mit Leerzeichen ("-bs 1") und Pfade mit Leerzeichen (der echte
    MODEL_PATH liegt unter "Application Support") müssen unverändert
    zurückkommen -- an genau diesen beiden Formen hängt die Decode-Migration."""
    _isolate_serverenv(tmp_path, monkeypatch)
    env = {"SERVER_BIN": "/x/whisper-server",
           "MODEL_PATH": "/Users/x/Library/Application Support/Quassel/m.bin",
           "WHISPER_THREADS": "8",
           "WHISPER_DECODE": "-bs 1"}
    config.write_serverenv(env)
    assert config.read_serverenv() == env


def test_a_failed_write_leaves_the_old_serverenv_intact(tmp_path, monkeypatch):
    """Der Grund für den Umweg über die Temp-Datei: schlägt das Schreiben
    fehl, darf die bestehende Datei nicht als leerer Rumpf zurückbleiben.
    Sonst verlöre der nächste Start die getroffene Modellwahl und suchte sich
    ein Modell neu."""
    _isolate_serverenv(tmp_path, monkeypatch)
    original = {"SERVER_BIN": "/x/whisper-server", "MODEL_PATH": "/m/gross.bin",
                "WHISPER_DECODE": "-bs 5"}
    config.write_serverenv(original)
    before = open(config.SERVERENV, encoding="utf-8").read()

    def boom(*a, **kw):
        raise OSError("Platte voll")
    monkeypatch.setattr(config.os, "replace", boom)
    try:
        config.write_serverenv({"SERVER_BIN": "/neu", "MODEL_PATH": "/m/klein.bin"})
    except OSError:
        pass
    assert open(config.SERVERENV, encoding="utf-8").read() == before
    assert config.read_serverenv() == original


def test_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    _isolate_serverenv(tmp_path, monkeypatch)
    config.write_serverenv({"WHISPER_DECODE": "-bs 1"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["server.env"]
