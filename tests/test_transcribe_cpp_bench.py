"""Messrahmen mit transcribe-cpp: Anbindung und Report (Prozess als Attrappe)."""
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bench import run_bench                          # noqa: E402
from quassel.backends import TranscribeCppBackend    # noqa: E402


def write_wav(path, seconds=0.2, rate=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    binary = tmp_path / "transcribe-cli"
    binary.write_text("#!/bin/sh\nexit 99\n")
    binary.chmod(0o755)
    model = tmp_path / "cohere-transcribe-03-2026-Q5_K_M.gguf"
    model.write_bytes(b"GGUF")
    write_wav(tmp_path / "clips" / "a.wav")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"version": 1, "items": [
        {"id": "a", "path": "clips/a.wav", "ref": "Hallo Welt",
         "language": "mixed", "category": "kurz"}]}), encoding="utf-8")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        out = ('{"type":"batch_header","load_ms":1.0}\n'
               '{"file":"x","text":" Hallo Welt","mel_ms":1.0,'
               '"encode_ms":1.0,"decode_ms":1.0}\n')
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(run_bench, "_dictionary_words", lambda: ["Quassel"])
    return tmp_path, binary, model, manifest, calls


def test_cli_flags_reach_the_adapter(setup):
    tmp, binary, model, _, _ = setup
    args = run_bench.parse_args(["--backend", "transcribe-cpp",
                                 "--transcribe-cpp", str(binary),
                                 "--tcpp-model", str(model),
                                 "--tcpp-family", "cohere",
                                 "--tcpp-compute", "metal"])
    (name, backend, is_whisper), = run_bench.select_backends(args)
    assert name == "transcribe-cpp" and is_whisper is False
    assert isinstance(backend, TranscribeCppBackend)
    assert backend.model == str(model) and backend.family.name == "cohere"
    assert backend.compute == "metal" and backend.health() is True


def test_unknown_family_is_rejected_by_argparse():
    with pytest.raises(SystemExit):
        run_bench.parse_args(["--tcpp-family", "erfunden"])


def test_old_namespace_without_tcpp_fields_still_works():
    from types import SimpleNamespace
    args = SimpleNamespace(backend="transcribe-cpp",
                           transcribe_cpp="/opt/transcribe-cli")
    (_, backend, _), = run_bench.select_backends(args)
    assert backend.health() is False          # ohne Modell sauber übersprungen


def test_report_names_model_family_and_quant(setup, capsys):
    tmp, binary, model, manifest, calls = setup
    json_out = tmp / "r.json"
    assert run_bench.main([str(manifest), "--root", str(tmp),
                           "--backend", "transcribe-cpp",
                           "--transcribe-cpp", str(binary),
                           "--tcpp-model", str(model),
                           "--json", str(json_out)]) == 0
    md = capsys.readouterr().out
    assert ("cohere-transcribe-03-2026-Q5_K_M.gguf (Familie cohere, "
            "Quantisierung Q5_K_M)") in md
    assert "transcribe.cpp: Familie cohere · Quantisierung Q5_K_M" in md
    report = json.loads(json_out.read_text(encoding="utf-8"))
    section = report["backends"][0]
    assert section["params"]["family"] == "cohere"
    assert section["params"]["quant"] == "Q5_K_M"
    assert section["params"]["model_file"] == model.name
    point = section["results"][0]
    assert point["hyp"] == "Hallo Welt" and point["wer"] == 0.0
    # mixed bei Cohere: Rückfall auf de, als Hinweis im Clip sichtbar
    assert any("Rückfall" in n for n in point["notes"])
    argv = next(c for c in calls if c[0] == str(binary))   # nicht git rev-parse
    assert argv[argv.index("-l") + 1] == "de"
    assert "--vocabulary-file" not in argv    # Cohere hat kein Wörterbuch


def test_adapter_error_classes_land_in_the_report(setup, monkeypatch):
    tmp, binary, model, manifest, _ = setup

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="model load: gguf load error\n")

    monkeypatch.setattr(subprocess, "run", failing)
    json_out = tmp / "r.json"
    run_bench.main([str(manifest), "--root", str(tmp),
                    "--backend", "transcribe-cpp",
                    "--transcribe-cpp", str(binary),
                    "--tcpp-model", str(model), "--json", str(json_out)])
    point = json.loads(json_out.read_text(encoding="utf-8"))["backends"][0]["results"][0]
    assert point["errors"] == ["model-load"] and point["ok"] is False


def test_qwen_gets_the_same_dictionary_as_the_whisper_server(setup, tmp_path):
    _, binary, _, _, calls = setup
    model = tmp_path / "Qwen3-ASR-1.7B-Q8_0.gguf"
    model.write_bytes(b"GGUF")
    args = run_bench.parse_args(["--backend", "transcribe-cpp",
                                 "--transcribe-cpp", str(binary),
                                 "--tcpp-model", str(model)])
    (_, backend, _), = run_bench.select_backends(args)
    assert backend.family.name == "qwen3-asr"
    model_label, params = run_bench.transcribe_cpp_meta(backend)
    assert "Quantisierung Q8_0" in model_label
    assert params["prompt"].startswith("Wörterbuch")
    write_wav(tmp_path / "b.wav")
    from types import SimpleNamespace
    from quassel.backends import TranscriptionOptions
    backend.transcribe(str(tmp_path / "b.wav"), TranscriptionOptions(
        SimpleNamespace(language="de"), language_hint="de"))
    assert "--vocabulary-file" in calls[-1]
