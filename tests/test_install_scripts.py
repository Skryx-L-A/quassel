"""Offline checks for the shell entrypoints used by the Linux installer."""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "bin" / "quassel-python"


def run_helper(*candidates):
    return subprocess.run(
        ["bash", str(HELPER), *map(str, candidates)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_python_helper_accepts_the_running_supported_python():
    result = run_helper(sys.executable)
    assert result.returncode == 0, result.stderr
    assert os.path.samefile(result.stdout.strip(), sys.executable)


def test_python_helper_rejects_an_unsupported_candidate(tmp_path):
    old_python = tmp_path / "python3.9"
    old_python.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    old_python.chmod(0o755)

    result = run_helper(old_python)
    assert result.returncode == 1
    assert "Python 3.10 oder neuer" in result.stderr


def test_python_launchers_use_the_version_checked_helper():
    for name in ("quasseld", "quassel-type", "quassel-pill", "quassel-mac"):
        content = (ROOT / "bin" / name).read_text(encoding="utf-8")
        assert "quassel-python" in content


def test_requirements_are_pinned_and_used_by_the_linux_installer():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    dependencies = [line for line in requirements.splitlines()
                    if line and not line.startswith("#")]
    assert dependencies
    assert all("==" in dependency for dependency in dependencies)

    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert '--requirement "$SRC/requirements.txt"' in installer


def test_ci_has_the_supported_platform_and_python_matrix():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert "ubuntu-latest, macos-latest, windows-latest" in workflow
    assert 'python: ["3.10", "3.11", "3.12", "3.13"]' in workflow
    assert "actions/setup-python@v6" in workflow
