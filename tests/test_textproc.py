"""Tests der Nachbearbeitung: Kommandos (undo/enter), Satzzeichen, Groß-/Klein."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from quassel import textproc


class Cfg:
    punctuation = True
    commands = True


def test_command_undo():
    assert textproc.is_command("scratch that") == "undo"
    assert textproc.is_command("lösch das") == "undo"
    assert textproc.is_command("Scratch that.") == "undo"      # Satzzeichen egal
    k, v = textproc.postprocess("scratch that", Cfg())
    assert (k, v) == ("command", "undo"), (k, v)


def test_command_enter():
    assert textproc.is_command("press enter") == "enter"
    assert textproc.is_command("drücke enter") == "enter"
    assert textproc.is_command("Press enter!") == "enter"
    k, v = textproc.postprocess("press enter", Cfg())
    assert (k, v) == ("command", "enter"), (k, v)


def test_not_a_command():
    assert textproc.is_command("press the red button") is None
    k, v = textproc.postprocess("press the red button", Cfg())
    assert k == "text"


def test_punctuation_and_caps():
    k, v = textproc.postprocess("hallo komma welt punkt", Cfg())
    assert (k, v) == ("text", "Hallo, welt."), (k, v)


def test_commands_disabled_passes_through():
    class C:
        punctuation = True
        commands = False
    k, v = textproc.postprocess("scratch that", C())
    assert k == "text", (k, v)


if __name__ == "__main__":
    for fn in [test_command_undo, test_command_enter, test_not_a_command,
               test_punctuation_and_caps, test_commands_disabled_passes_through]:
        fn(); print("ok:", fn.__name__)
    print("ALL TEXTPROC TESTS PASSED")
