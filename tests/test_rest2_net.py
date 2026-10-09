"""net.download ohne Netz (L139): curl- und urllib-Weg, Prüfsumme, Abbruch,
Aufräumen der .part-Datei. curl wird nie gestartet, nichts heruntergeladen."""
import hashlib
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel import net


class Curl:
    """Popen-Ersatz für curl: schreibt beim Start den Inhalt nach -o."""

    def __init__(self, payload=b"model", code=0, runs=False):
        self.payload, self.code, self.runs = payload, code, runs
        self.argv = None
        self.terminated = False
        self.returncode = None

    def __call__(self, argv, **_kw):
        self.argv = argv
        out = argv[argv.index("-o") + 1]
        if self.payload is not None:
            with open(out, "wb") as f:
                f.write(self.payload)
        return self

    def poll(self):
        if self.runs and not self.terminated:
            return None
        self.returncode = -15 if self.terminated else self.code
        return self.returncode

    def terminate(self):
        self.terminated = True


@pytest.fixture
def curl(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(net, "_content_length", lambda url: 0)

    def use(**kw):
        fake = Curl(**kw)
        monkeypatch.setattr(net.subprocess, "Popen", fake)
        return fake
    return use


def test_curl_success_moves_the_part_file_into_place(tmp_path, curl):
    fake = curl(payload=b"custom model")
    target = tmp_path / "models" / "my-model.bin"
    seen = []
    assert net.download("https://example.invalid/m.bin", str(target), seen.append)
    assert target.read_bytes() == b"custom model"
    assert not (tmp_path / "models" / "my-model.bin.part").exists()
    assert seen[-1] == 1.0
    # Wiederaufnahme und Abbruch bei toter Leitung sind eingeschaltet.
    assert "-C" in fake.argv and "--fail" in fake.argv and "--speed-limit" in fake.argv


def test_known_model_with_wrong_hash_is_rejected_and_removed(tmp_path, curl):
    curl(payload=b"not the real model")
    target = tmp_path / "ggml-tiny.bin"
    assert net.download("https://example.invalid/t", str(target)) is False
    assert not target.exists()
    assert not (tmp_path / "ggml-tiny.bin.part").exists()


def test_known_model_with_matching_hash_is_accepted(tmp_path, curl, monkeypatch):
    payload = b"pretend weights"
    monkeypatch.setitem(net.SHA256, "ggml-tiny.bin", hashlib.sha256(payload).hexdigest())
    curl(payload=payload)
    target = tmp_path / "ggml-tiny.bin"
    assert net.download("https://example.invalid/t", str(target))
    assert target.read_bytes() == payload


def test_http_error_leaves_no_files(tmp_path, curl):
    curl(payload=b"<html>404</html>", code=22)
    target = tmp_path / "x.bin"
    assert net.download("https://example.invalid/x", str(target)) is False
    assert not target.exists() and not (tmp_path / "x.bin.part").exists()


def test_empty_download_counts_as_failure(tmp_path, curl):
    curl(payload=b"")
    target = tmp_path / "x.bin"
    assert net.download("https://example.invalid/x", str(target)) is False
    assert not target.exists()


def test_cancel_terminates_curl_and_keeps_the_old_target(tmp_path, curl):
    fake = curl(payload=b"half", runs=True)
    target = tmp_path / "x.bin"
    target.write_bytes(b"previous model")
    cancel = threading.Event()
    cancel.set()
    assert net.download("https://example.invalid/x", str(target), cancel=cancel) is False
    assert fake.terminated
    assert target.read_bytes() == b"previous model"


def test_without_curl_urllib_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda name: None)

    def retrieve(url, path, hook):
        with open(path, "wb") as f:
            f.write(b"data")
        if hook:
            hook(1, 4, 4)
        return path, None
    monkeypatch.setattr(net.urllib.request, "urlretrieve", retrieve)
    target = tmp_path / "x.bin"
    seen = []
    assert net.download("https://example.invalid/x", str(target), seen.append)
    assert target.read_bytes() == b"data"
    assert seen and seen[-1] == 1.0


def test_without_curl_a_network_error_is_a_clean_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda name: None)

    def retrieve(url, path, hook):
        with open(path, "wb") as f:
            f.write(b"par")
        raise OSError("connection reset")
    monkeypatch.setattr(net.urllib.request, "urlretrieve", retrieve)
    target = tmp_path / "x.bin"
    assert net.download("https://example.invalid/x", str(target)) is False
    assert not target.exists() and not (tmp_path / "x.bin.part").exists()


class Headers:
    def __init__(self, text):
        self.stdout = text


def test_content_length_reads_a_plain_response(monkeypatch):
    monkeypatch.setattr(net.subprocess, "run", lambda *a, **k: Headers(
        "HTTP/2 200\r\ncontent-length: 1234\r\n\r\n"))
    assert net._content_length("https://example.invalid/x") == 1234


def test_content_length_without_header_or_curl_is_zero(monkeypatch):
    monkeypatch.setattr(net.subprocess, "run", lambda *a, **k: Headers("HTTP/2 200\r\n"))
    assert net._content_length("https://example.invalid/x") == 0

    def boom(*a, **k):
        raise OSError("no curl")
    monkeypatch.setattr(net.subprocess, "run", boom)
    assert net._content_length("https://example.invalid/x") == 0


@pytest.mark.xfail(strict=True, reason=(
    "Fehler in net.py (nicht in diesem Auftrag): bei Weiterleitungen (Hugging "
    "Face antwortet 302 -> CDN) zählt die Länge der ERSTEN Antwort, nicht die "
    "der Datei; der Fortschritt springt sofort auf 100 %."))
def test_content_length_after_a_redirect_is_the_final_one(monkeypatch):
    monkeypatch.setattr(net.subprocess, "run", lambda *a, **k: Headers(
        "HTTP/2 302\r\ncontent-length: 1300\r\nlocation: https://cdn/x\r\n\r\n"
        "HTTP/2 200\r\ncontent-length: 574041195\r\n\r\n"))
    assert net._content_length("https://example.invalid/x") == 574041195
