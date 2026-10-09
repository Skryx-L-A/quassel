"""`quassel-type diagnostics` druckt den Diagnosebericht (offline, Attrappen)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import cli, diagnostics  # noqa: E402


def test_diagnostics_command_prints_report(monkeypatch, capsys):
    monkeypatch.setattr(diagnostics, "build_report", lambda: "Quassel diagnostics\nok\n")
    assert cli.main(["diagnostics"]) == 0
    assert capsys.readouterr().out == "Quassel diagnostics\nok\n"


def test_diagnostics_is_listed_in_help():
    assert "diagnostics" in cli.parser().format_help()
