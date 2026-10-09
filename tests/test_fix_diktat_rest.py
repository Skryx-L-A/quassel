"""Review 2026-10-09, Befund 5: eine Ausnahme im langen Teil des Diktats
rettet die Aufnahme wie ein Fehlschlag, statt sie still zu verwerfen und die
Pille auf „transkribiert" stehen zu lassen."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from test_dictation_privacy import setup  # noqa: E402


def test_exception_while_pasting_rescues_and_reports(monkeypatch, tmp_path):
    core = setup(monkeypatch, tmp_path)

    def broken(text):
        raise RuntimeError("Pasteboard weg")
    core._paste = broken
    assert core.start_recording()
    core.finish_now()
    assert core.states[-1][0] == "error"
    left = sorted(os.listdir(tmp_path))
    assert len(left) == 1 and left[0].startswith("rescued-")


def test_full_disk_while_writing_the_wav_keeps_the_raw_file(monkeypatch, tmp_path):
    core = setup(monkeypatch, tmp_path)

    def unwritable(data, path):
        raise OSError("Platte voll")
    core._wav_from_raw = unwritable
    assert core.start_recording()
    core.finish_now()
    assert core.states[-1][0] == "error"
    assert os.path.exists(core.rec.path)          # letzte Kopie des Diktats


def test_failing_rescue_path_still_ends_in_error(monkeypatch, tmp_path):
    core = setup(monkeypatch, tmp_path)

    def broken(text):
        raise RuntimeError("Pasteboard weg")
    core._paste = broken

    def no_rescue(data):
        raise RuntimeError("auch kaputt")
    core._rescue = no_rescue
    assert core.start_recording()
    core.finish_now()
    assert core.states[-1][0] == "error"
    assert os.path.exists(core.rec.path)
