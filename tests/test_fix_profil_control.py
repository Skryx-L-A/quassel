"""Profile control must never address the default Linux services."""
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="bash bin/quassel-ctl mit #!/bin/sh-Shim und PATH /usr/bin:/bin")

ROOT = Path(__file__).resolve().parents[1]


def _ctl(tmp_path, command, profile):
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "systemctl.log"
    systemctl = fake / "systemctl"
    systemctl.write_text('#!/bin/sh\necho "$*" >> "$QUASSEL_TEST_LOG"\n')
    systemctl.chmod(0o755)
    env = {"PATH": f"{fake}:/usr/bin:/bin", "QUASSEL_TEST_LOG": str(log),
           "QUASSEL_PROFILE": profile, "WAYLAND_DISPLAY": "wayland-test"}
    result = subprocess.run(["bash", str(ROOT / "bin/quassel-ctl"), command],
                            env=env, capture_output=True, text=True, timeout=10)
    return result, log.read_text().splitlines() if log.exists() else []


def test_profile_control_addresses_only_its_units(tmp_path):
    result, calls = _ctl(tmp_path, "start", "next")
    assert result.returncode == 0
    assert calls == ["--user import-environment WAYLAND_DISPLAY",
                     "--user start quasseld@next.service quassel-server@next.service quassel-pill@next.service"]


def test_profile_stop_keeps_shared_ydotoold(tmp_path):
    result, calls = _ctl(tmp_path, "stop", "next")
    assert result.returncode == 0
    assert calls == ["--user stop quasseld@next.service quassel-server@next.service"]


def test_invalid_profile_fails_before_systemctl(tmp_path):
    result, calls = _ctl(tmp_path, "panic", "bad/name")
    assert result.returncode == 2
    assert "QUASSEL_PROFILE" in result.stderr
    assert calls == []


def test_default_control_keeps_existing_service_names(tmp_path):
    result, calls = _ctl(tmp_path, "start", "")
    assert result.returncode == 0
    assert calls == ["--user import-environment WAYLAND_DISPLAY",
                     "--user start quasseld quassel-server quassel-pill"]
