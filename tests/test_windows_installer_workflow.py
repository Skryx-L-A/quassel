"""Textual checks for the CI workflow that builds the Windows installer."""
import re
from pathlib import Path

import quassel


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "windows-installer.yml"


def _workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_builds_on_windows_with_python_313():
    text = _workflow()
    assert "runs-on: windows-latest" in text
    assert 'python-version: "3.13"' in text
    assert "workflow_dispatch:" in text
    assert 'tags: ["v*"]' in text


def test_workflow_runs_pyinstaller_clean_with_the_spec_and_inno_setup():
    text = _workflow()
    assert "--noconfirm --clean" in text
    assert "windows\\quassel.spec" in text
    assert "windows\\quassel.iss" in text
    assert "/DMyAppVersion=" in text
    assert "/DUnverifiedBuild=1" in text


def test_workflow_uploads_the_installer_and_its_checksum_as_artifact():
    text = _workflow()
    assert "actions/upload-artifact@" in text
    assert "installer/Quassel-Setup.exe\n" in text
    assert "installer/Quassel-Setup.exe.sha256" in text
    assert "if-no-files-found: error" in text


def test_workflow_stays_read_only_and_never_touches_a_release():
    text = _workflow()
    assert re.search(r"^permissions:\n  contents: read\n", text, re.M)
    assert "contents: write" not in text
    assert "gh release" not in text
    assert "GITHUB_TOKEN" not in text


def test_workflow_does_not_start_the_app():
    # The exe opens a window and listens to the keyboard; the smoke test only
    # looks at files.
    text = _workflow()
    assert "Start-Process" not in text
    assert "Quassel.exe --" not in text
    assert "Quassel.exe\"" in text   # existence check only


def test_build_dependencies_are_pinned():
    lines = (ROOT / "windows" / "requirements-build.txt").read_text(
        encoding="utf-8").splitlines()
    dependencies = [line for line in lines
                    if line and not line.startswith(("#", "-r "))]
    assert dependencies
    assert all("==" in line for line in dependencies)
    assert "-r ../requirements.txt" in lines
    assert "windows/requirements-build.txt" in _workflow()


def test_installer_script_takes_the_version_from_the_build():
    iss = (ROOT / "windows" / "quassel.iss").read_text(encoding="utf-8")
    assert "#ifndef MyAppVersion" in iss
    default = re.search(r'#define MyAppVersion "([^"]+)"', iss).group(1)
    assert default == quassel.__version__
    assert "OutputBaseFilename=Quassel-Setup\n" in iss


def test_installer_marks_the_automatic_build_in_both_languages():
    iss = (ROOT / "windows" / "quassel.iss").read_text(encoding="utf-8")
    assert "#ifdef UnverifiedBuild" in iss
    assert "built automatically" in iss
    assert "automatisch gebaut" in iss
    assert "{#NoteEn}" in iss and "{#NoteDe}" in iss
