"""Statische Prüfungen für Bauskript und Modellliste von transcribe.cpp.

Das Skript wird nie ausgeführt (schwerer Lauf); geprüft wird nur, dass Tag,
Commit und Hashes zusammenpassen und die Syntax stimmt.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quassel.backends import tcpp_families as fams

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "tcpp" / "build_transcribe_cpp.sh"
README = ROOT / "scripts" / "tcpp" / "README.md"


def test_script_pins_the_same_tag_and_commit_as_the_adapter():
    text = SCRIPT.read_text(encoding="utf-8")
    assert f'TAG="{fams.UPSTREAM_TAG}"' in text
    assert f'COMMIT="{fams.UPSTREAM_COMMIT}"' in text
    assert "rev-parse HEAD" in text            # Tag-Verschiebung fällt auf


def test_script_stays_out_of_global_locations():
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    text = "\n".join(l for l in lines if not l.lstrip().startswith("#"))
    assert 'DEST="${HOME}/AI/quassel-bench-data/tcpp"' in text
    for forbidden in ("sudo", "/usr/local", "make install", "--target install",
                      "pip install", "brew install", "apt-get", "apt install"):
        assert forbidden not in text


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt",
                    reason="bash -n nur auf Unix")
def test_script_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_readme_lists_both_quants_with_pinned_urls_and_hashes():
    text = README.read_text(encoding="utf-8")
    urls = re.findall(r"https://huggingface\.co/handy-computer/\S+?\.gguf", text)
    names = {u.rsplit("/", 1)[-1] for u in urls}
    for model in ("cohere-transcribe-03-2026", "Qwen3-ASR-1.7B",
                  "canary-1b-v2", "parakeet-tdt-0.6b-v3"):
        for quant in ("Q8_0", "Q5_K_M"):
            assert f"{model}-{quant}.gguf" in names
    for url in urls:
        # an eine 40-stellige Revision gebunden, nicht an "main"
        assert re.search(r"/resolve/[0-9a-f]{40}/", url), url
    hashes = re.findall(r"`([0-9a-f]{64})`", text)
    assert len(set(hashes)) == 8


def test_readme_families_are_known_to_the_adapter():
    text = README.read_text(encoding="utf-8")
    for name in re.findall(r"\| `([a-z0-9-]+)` \| Q", text):
        assert fams.family(name).name == name
