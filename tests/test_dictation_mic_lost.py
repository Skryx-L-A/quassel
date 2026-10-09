"""Abgerissene Aufnahme (D65): das Diktat meldet sie sichtbar, statt still
„zu kurz" oder einen halben Text zu liefern. Ohne Mikrofon, Server, Fenster."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from quassel import dictation, i18n
from tests.test_dictation_ui import Core, pumps  # noqa: F401 — Fixture


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def lost_core(data, reason="stalled"):
    core = Core(data=data)
    core.rec.interrupted = reason
    return core


def test_lost_microphone_with_no_audio_is_an_error_not_too_short(pumps):
    core = lost_core(b"\1\0" * 10)
    core.start_recording()
    assert core._finish_capture() is None
    assert core.states[-1] == ("error", i18n.tr("err_mic_lost"))
    assert core.notes == [i18n.tr("err_mic_lost")]


def test_lost_microphone_after_some_audio_still_transcribes_and_says_so(pumps):
    core = lost_core(b"\1\0" * 16000, reason="ended")
    core.start_recording()
    assert core._finish_capture() is not None
    assert core.notes == [i18n.tr("err_mic_lost_partial")]


def test_healthy_recording_reports_nothing(pumps):
    core = lost_core(b"\1\0" * 10, reason=None)
    core.start_recording()
    assert core._finish_capture() is None
    assert core.states[-1] == ("done", i18n.tr("too_short"))
    assert core.notes == []
