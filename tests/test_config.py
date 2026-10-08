"""dictionary_words() cacht die Datei über mtime+size — ein Teiltranskript
liest sie nicht mehr bei jedem Aufruf neu von der Platte. dictionary_save()
verwirft den Cache trotzdem explizit (siehe Kommentar in config.py)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import config


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "DICTIONARY", str(tmp_path / "dictionary.txt"))
    monkeypatch.setattr(config, "LEARN_COUNTS", str(tmp_path / "dictionary-counts.json"))
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


def _isolate_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFDIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "config.ini"))


def test_beep_output_defaults_to_system(tmp_path, monkeypatch):
    _isolate_cfg(tmp_path, monkeypatch)
    assert config.Cfg().beep_output == "system"


def test_beep_output_reads_saved_device(tmp_path, monkeypatch):
    _isolate_cfg(tmp_path, monkeypatch)
    config.save({("behavior", "beep_output"): "Bluetooth-Kopfhörer"})
    assert config.Cfg().beep_output == "Bluetooth-Kopfhörer"


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


# ------------------------------------------------- config.ini robust (B29)
def _cfg_file(tmp_path, monkeypatch, text=None):
    _isolate_cfg(tmp_path, monkeypatch)
    if text is not None:
        (tmp_path / "config.ini").write_text(text, encoding="utf-8")
    return tmp_path / "config.ini"


def test_a_percent_sign_survives_save_and_read(tmp_path, monkeypatch):
    """"%" ist bei ConfigParser die Interpolation: Speichern warf ValueError,
    Lesen InterpolationSyntaxError -- mit Wake-Phrase oder KI-Endpunkt darin."""
    _cfg_file(tmp_path, monkeypatch)
    config.save({("wakeword", "phrase"): "100% Quassel",
                 ("ai", "endpoint"): "http://127.0.0.1:11434/%25x"})
    cfg = config.Cfg()
    assert cfg.wakeword_phrase == "100% Quassel"
    assert cfg.ai_endpoint == "http://127.0.0.1:11434/%25x"


def test_a_percent_sign_typed_by_hand_is_read(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch, "[wakeword]\nphrase = Hallo %s Welt\n")
    assert config.Cfg().wakeword_phrase == "Hallo %s Welt"


def test_decimal_comma_is_read_as_a_number(tmp_path, monkeypatch):
    """`hold_min = 0,5` warf ValueError in Cfg(): der Daemon startete nicht."""
    _cfg_file(tmp_path, monkeypatch, "[hotkey]\nhold_min = 0,7\ndouble_window = 0,3\n")
    cfg = config.Cfg()
    assert cfg.hold_min == 0.7 and cfg.double_window == 0.3


def test_nonsense_numbers_fall_back_to_the_default(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch,
              "[hotkey]\nhold_min = abc\ndouble_window = nan\n"
              "[pill]\nscale = \npos_x = 1,5\npos_y = x\n"
              "[ai]\ntimeout = -5\n")
    cfg = config.Cfg()
    assert cfg.hold_min == 0.5
    assert cfg.double_window == 0.45
    assert cfg.pill_scale == 1.0
    assert (cfg.pill_pos_x, cfg.pill_pos_y) == (-1, -1)
    assert cfg.ai_timeout == 60.0


def test_out_of_range_numbers_are_rejected_or_clamped(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch,
              "[hotkey]\nhold_min = -1\ndouble_window = 1e9\n"
              "[pill]\nscale = 9\nopacity = 0\n"
              "[ai]\ntimeout = inf\n")
    cfg = config.Cfg()
    assert cfg.hold_min == 0.5 and cfg.double_window == 0.45   # unsinnig -> Vorgabe
    assert cfg.pill_scale == 2.0 and cfg.pill_opacity == 0.15  # wie die Pille selbst
    assert cfg.ai_timeout == 60.0


def test_valid_numbers_pass_through_unchanged(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch,
              "[hotkey]\nhold_min = 0.8\ndouble_window = 0.6\n"
              "[pill]\nscale = 1.5\nopacity = 0.5\npos_x = 120\npos_y = -3\n"
              "[ai]\ntimeout = 120\n")
    cfg = config.Cfg()
    assert (cfg.hold_min, cfg.double_window) == (0.8, 0.6)
    assert (cfg.pill_scale, cfg.pill_opacity) == (1.5, 0.5)
    assert (cfg.pill_pos_x, cfg.pill_pos_y) == (120, -3)
    assert cfg.ai_timeout == 120.0


def test_a_garbled_boolean_falls_back(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch,
              "[speech]\npunctuation = vielleicht\n[history]\nenabled = off\n")
    cfg = config.Cfg()
    assert cfg.punctuation is True          # Vorgabe
    assert cfg.history_enabled is False     # gültiger Wert bleibt


def test_an_unreadable_config_gives_the_defaults_and_can_be_saved(tmp_path, monkeypatch):
    path = _cfg_file(tmp_path, monkeypatch)
    path.write_bytes(b"\xff\xfe\x00 kein INI \x80")
    assert config.Cfg().hold_min == 0.5
    config.save({("hotkey", "chord"): "alt+meta"})     # darf nicht werfen
    assert config.Cfg().chord == "alt+meta"


def test_non_ascii_values_round_trip(tmp_path, monkeypatch):
    """save() schreibt UTF-8; gelesen wurde in der Sprache des Systems (unter
    Windows cp1252) -- Umlaute in der Wake-Phrase kamen als Zeichensalat zurück."""
    _cfg_file(tmp_path, monkeypatch)
    config.save({("wakeword", "phrase"): "Hallo Größe Ärger"})
    assert config.Cfg().wakeword_phrase == "Hallo Größe Ärger"


# ------------------------------------------------- history.jsonl nur für den Nutzer (A5)
def _isolate_history(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATADIR", str(tmp_path))
    monkeypatch.setattr(config, "HISTORY", str(tmp_path / "history.jsonl"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX-Rechte")
def test_history_is_readable_by_the_user_only(tmp_path, monkeypatch):
    """Die letzten Diktate (auch Privates, Passwörter) lagen mit der Standard-
    umask da: 0644, also für jeden Nutzer des Rechners lesbar."""
    _isolate_history(tmp_path, monkeypatch)
    old_umask = os.umask(0o022)
    try:
        config.history_append("geheim")
    finally:
        os.umask(old_umask)
    assert (os.stat(config.HISTORY).st_mode & 0o777) == 0o600
    assert config.history_read()[0]["text"] == "geheim"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-Rechte")
def test_an_existing_world_readable_history_is_tightened(tmp_path, monkeypatch):
    """Installationen mit einem alten Verlauf ziehen beim nächsten Diktat nach."""
    _isolate_history(tmp_path, monkeypatch)
    with open(config.HISTORY, "w", encoding="utf-8") as f:
        f.write('{"text": "alt", "ts": %s}\n' % time.time())
    os.chmod(config.HISTORY, 0o644)
    config.history_append("neu")
    assert (os.stat(config.HISTORY).st_mode & 0o777) == 0o600
    assert [e["text"] for e in config.history_read()] == ["alt", "neu"]


def test_history_default_stays_on(tmp_path, monkeypatch):
    """Ob der Verlauf standardmäßig an ist, entscheidet der Betreiber, nicht
    dieser Änderung: die Vorgabe bleibt wie sie war."""
    _isolate_cfg(tmp_path, monkeypatch)
    assert config.Cfg().history_enabled is True


def test_history_expires_on_read_and_write(tmp_path, monkeypatch):
    _isolate_history(tmp_path, monkeypatch)
    with open(config.HISTORY, "w", encoding="utf-8") as f:
        f.write('{"text":"old","ts":1}\n')
        f.write('{"text":"new","ts":%s}\n' % time.time())
    assert [e["text"] for e in config.history_read(history_days=7)] == ["new"]
    config.history_append("latest", history_days=7)
    assert [e["text"] for e in config.history_read(history_days=7)] == ["new", "latest"]


def test_old_config_is_migrated_once_and_save_merges_fresh_values(tmp_path, monkeypatch):
    path = _cfg_file(tmp_path, monkeypatch, "[speech]\nlanguage=de\n")
    assert config.migrate() is True
    assert config.migrate() is False
    assert "version = 1" in path.read_text(encoding="utf-8")
    config.save({("speech", "language"): "en"})
    config.save({("history", "history_days"): 0})
    cfg = config.Cfg()
    assert (cfg.language, cfg.history_days) == ("en", 0)


# ------------------------------------------- Auto-Wörterbuch schreibt mit Merge (B22)
def test_learning_reads_the_file_fresh_and_keeps_what_was_added_meanwhile(tmp_path, monkeypatch):
    """Der Daemon lernte aus seiner Kopie und schrieb sie über die Datei: ein
    Eintrag, den der Nutzer im Kontrollzentrum inzwischen ergänzt hatte, war
    weg. Selbst der mtime/size-Cache verrät das nicht, wenn der Austausch
    dieselbe Länge hat und in dieselbe Zeitauflösung fällt."""
    _isolate(tmp_path, monkeypatch)
    config.dictionary_save("Alpha\nBeta")
    assert config.dictionary_words() == ["Alpha", "Beta"]      # Cache gefüllt
    st = os.stat(config.DICTIONARY)
    with open(config.DICTIONARY, "w", encoding="utf-8") as f:   # von außen ersetzt
        f.write("Alpha\nGamma")                                # gleiche Länge
    os.utime(config.DICTIONARY, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert config.dictionary_learn("Ich nutze PyTorch") == []
    assert config.dictionary_learn("Ich nutze PyTorch") == ["PyTorch"]
    assert config.dictionary_words() == ["Alpha", "Gamma", "PyTorch"]


def test_learning_writes_nothing_when_nothing_is_new(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert config.dictionary_learn("Der Hund ist schnell") == []
    assert not os.path.exists(config.DICTIONARY)
    config.dictionary_save("PyTorch")
    before = os.stat(config.DICTIONARY).st_mtime_ns
    assert config.dictionary_learn("PyTorch und pytorch") == []
    assert os.stat(config.DICTIONARY).st_mtime_ns == before


def test_learning_never_drops_manual_entries(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    manual = ["Eigenname%d" % i for i in range(500)]
    config.dictionary_save("\n".join(manual))
    assert config.dictionary_learn("Ich nutze PyTorch und NASA") == []
    assert config.dictionary_words() == manual


def test_two_learners_at_once_lose_no_word(tmp_path, monkeypatch):
    import threading
    _isolate(tmp_path, monkeypatch)

    def learn_many(prefix):
        for i in range(25):
            config.dictionary_learn("Wort %sX%d" % (prefix, i))
            config.dictionary_learn("Wort %sX%d" % (prefix, i))
    threads = [threading.Thread(target=learn_many, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    words = config.dictionary_words()
    assert len(words) == 50 and len(set(words)) == 50
