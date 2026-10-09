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


def test_online_and_offline_installers_ship_the_cli_launcher():
    online = (ROOT / "install.sh").read_text(encoding="utf-8")
    offline = (ROOT / "scripts/offline/bundle-files/install.sh").read_text(
        encoding="utf-8")
    assert '"$SRC/bin/quassel-type-cli"' in online
    assert '"$BIN/quassel-type-cli"' in offline
    assert 'quassel.cli' in offline


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


def test_ci_checks_every_shell_entrypoint_without_masking_shellcheck():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert "find install.sh uninstall.sh bin scripts" in workflow
    assert 'bash -n "$script"' in workflow
    assert 'shellcheck -S warning "$script"' in workflow
    assert "shellcheck -S warning install.sh uninstall.sh bin/quassel-ctl || true" \
        not in workflow


def test_ci_runs_the_full_offline_pytest_suite():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert "python -m pytest -q tests --ignore=tests/test_coreaudio_mac.py" \
        in workflow


def test_uninstaller_removes_every_user_file_and_asks_before_system_changes():
    uninstaller = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
    for launcher in ("quasseld", "quassel-type", "quassel-pill", "quassel-ctl",
                     "quassel-python", "quassel-type-cli"):
        assert f'"$BIN"/{launcher}' in uninstaller
    assert "quassel-voice.svg" in uninstaller
    assert "quassel-voice.png" in uninstaller
    assert "read -r -p" in uninstaller
    assert "gpasswd -d" in uninstaller


def test_online_installer_refuses_to_fall_back_to_main():
    installer = (ROOT / "scripts" / "quassel-install.sh").read_text(
        encoding="utf-8")
    assert "archive/refs/tags/$TAG.tar.gz" in installer
    assert "archive/refs/heads/main.tar.gz" not in installer
    assert "Release-Tag konnte nicht sicher ermittelt werden." in installer


def test_model_downloads_only_publish_complete_files():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'part="$DATA/models/$f.part"' in installer
    assert 'mv "$part" "$target"' in installer
    assert 'VAD_PART="$VAD_PATH.part"' in installer
    assert 'mv "$VAD_PART" "$VAD_PATH"' in installer


def test_macos_installer_requires_a_checksum_unless_explicitly_overridden():
    installer = (ROOT / "scripts" / "quassel-install-macos.sh").read_text(
        encoding="utf-8")
    assert "ALLOW_UNVERIFIED=0" in installer
    assert "--allow-unverified" in installer
    assert 'if [[ "$ALLOW_UNVERIFIED" -eq 1 ]]' in installer
    assert "Prüfsummendatei fehlt" in installer


def test_readme_matches_the_current_platform_behavior():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "real microphone waveform" not in readme
    assert "turning Quassel off frees the GPU memory" not in readme
    assert "Windows build is fresh" not in readme
    assert "latest (**v2.7.0**)" in readme


def test_install_guide_lists_current_models_and_python_minimum():
    guide = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    for model in ("base-q5_1", "small-q5_1", "medium-q5_0",
                  "large-v3-turbo-q5_0", "large-v3"):
        assert model in guide
    assert "Python 3.10" in guide


# --------------------------------------------------------------------------
# Linux installer: engine packages, pinned whisper.cpp, --model
# --------------------------------------------------------------------------
import hashlib
import shutil
import io
import stat
import tarfile
import textwrap

import pytest

LIB = ROOT / "scripts" / "install-lib.sh"
PIN = ROOT / "scripts" / "whisper-cpp-pin.sh"
TAG = "v9.9.9"
API_KEY = f"api.github.com_repos_Skryx-L-A_quassel_releases_tags_{TAG}"
DL_PREFIX = f"github.com_Skryx-L-A_quassel_releases_download_{TAG}_"

CURL_SHIM = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Attrappe: liefert Dateien aus $FIXTURES statt aus dem Netz.
    out=""; url=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -o) out="$2"; shift 2 ;;
            -H|--max-time) shift 2 ;;
            -*) shift ;;
            *) url="$1"; shift ;;
        esac
    done
    echo "$url" >> "$FIXTURES/curl.log"
    key="${url#*://}"; key="${key//\\//_}"
    [[ -f "$FIXTURES/$key" ]] || exit 22
    if [[ -n "$out" ]]; then cp "$FIXTURES/$key" "$out"; else cat "$FIXTURES/$key"; fi
""")


def _shim(directory, name, body):
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _tarball(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class Release:
    """A fake GitHub release served by the curl shim."""

    def __init__(self, tmp_path):
        self.fixtures = tmp_path / "fixtures"
        self.fixtures.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.data = tmp_path / "data"
        self.data.mkdir()
        _shim(self.bin, "curl", CURL_SHIM.split("\n", 1)[1])
        _shim(self.bin, "uname", 'if [[ "${1:-}" == "-m" ]]; then echo x86_64; '
              'else /usr/bin/uname "$@"; fi\n')
        self.assets = []

    def add_asset(self, evar, members=None, checksum="file", good_sum=True):
        asset = f"quassel-engine-linux-{evar}-x86_64.tar.gz"
        payload = _tarball(members or {
            f"{evar}/whisper-server": b"#!/bin/sh\necho engine\n",
            f"{evar}/libggml.so.0": b"lib\n"})
        (self.fixtures / (DL_PREFIX + asset)).write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        if not good_sum:
            digest = "0" * 64
        if checksum == "file":
            (self.fixtures / (DL_PREFIX + asset + ".sha256")).write_text(
                f"{digest}  {asset}\n")
        elif checksum == "sums":
            (self.fixtures / (DL_PREFIX + "SHA256SUMS.txt")).write_text(
                f"{digest}  {asset}\n")
        self.assets.append(asset)

    def publish(self):
        names = ",".join(f'{{"name": "{a}"}}' for a in self.assets)
        (self.fixtures / API_KEY).write_text(
            '{"tag_name": "%s", "assets": [%s]}' % (TAG, names))

    def run(self, body, extra_env=None):
        script = textwrap.dedent("""\
            set -u
            warn() { echo "WARN: $*" >&2; }
            ok() { echo "OK: $*"; }
            source "$LIB_PIN"
            source "$LIB_FILE"
            SRC="$ROOT_DIR"; DATA="$DATA_DIR"
            HAS_GPU=0; SERVER_BIN_PATH=""; ENGINE_DESC=""
        """) + textwrap.dedent(body)
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FIXTURES": str(self.fixtures),
            "LIB_PIN": str(PIN), "LIB_FILE": str(LIB),
            "ROOT_DIR": str(ROOT), "DATA_DIR": str(self.data),
            "QUASSEL_RELEASE_TAG": TAG,
            "HOME": str(self.data),
        }
        env.update(extra_env or {})
        return subprocess.run(["bash", "-c", script], env=env, text=True,
                              capture_output=True, check=False)

    def downloads(self):
        log = self.fixtures / "curl.log"
        return log.read_text().splitlines() if log.exists() else []


def _need_posix_shell():
    if sys.platform == "win32":
        pytest.skip("the Linux installer scripts are exercised on POSIX hosts")


@pytest.fixture
def release(tmp_path):
    _need_posix_shell()
    return Release(tmp_path)


def test_release_tag_comes_from_env_or_the_package_version(release):
    result = release.run('lib_release_tag "$SRC"')
    assert result.stdout.strip() == TAG
    version = (ROOT / "quassel" / "__init__.py").read_text(encoding="utf-8")
    ver = version.split('__version__ = "', 1)[1].split('"', 1)[0]
    result = release.run('lib_release_tag "$SRC"', {"QUASSEL_RELEASE_TAG": ""})
    assert result.stdout.strip() == f"v{ver}"
    result = release.run('lib_release_tag "$SRC"',
                         {"QUASSEL_RELEASE_TAG": "main; rm -rf /"})
    assert result.returncode == 1


def test_prebuilt_falls_back_to_source_when_the_release_has_no_package(release):
    # The v2.5.0-v2.7.0 situation: the release exists, but without engines.
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=1" in result.stdout
    assert "baue aus dem Quelltext" in result.stderr
    assert not any("/download/" in url for url in release.downloads())
    assert not (release.data / "engine").exists()


def test_prebuilt_falls_back_when_the_release_lookup_fails(release):
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=1" in result.stdout
    assert "baue aus dem Quelltext" in result.stderr


def test_prebuilt_installs_a_verified_engine_and_wrapper(release):
    release.add_asset("cpu")
    release.publish()
    result = release.run(
        'install_prebuilt_engine; echo "rc=$? $SERVER_BIN_PATH $HAS_GPU"')
    assert "rc=0" in result.stdout, result.stderr
    engine = release.data / "engine" / "cpu"
    assert (engine / "whisper-server").exists()
    assert (engine / ".release").read_text().strip() == TAG
    assert f"{engine}/run-server.sh 0" in result.stdout
    assert "LD_LIBRARY_PATH" in (engine / "run-server.sh").read_text()
    assert os.access(engine / "run-server.sh", os.X_OK)
    assert not list((release.data / "engine").glob(".new*"))


def test_prebuilt_accepts_a_checksum_from_sha256sums(release):
    release.add_asset("cpu", checksum="sums")
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=0" in result.stdout, result.stderr


def test_prebuilt_prefers_cuda_with_an_nvidia_gpu_and_sets_has_gpu(release):
    _shim(release.bin, "nvidia-smi", "exit 0\n")
    release.add_asset("cuda")
    release.add_asset("cpu")
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$? $HAS_GPU"')
    assert "rc=0 1" in result.stdout, result.stderr
    assert (release.data / "engine" / "cuda" / "whisper-server").exists()
    assert not (release.data / "engine" / "cpu").exists()


def test_prebuilt_uses_cpu_when_only_that_package_exists(release):
    _shim(release.bin, "nvidia-smi", "exit 0\n")
    release.add_asset("cpu")
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$? $HAS_GPU"')
    assert "rc=0 0" in result.stdout, result.stderr


def test_prebuilt_rejects_a_wrong_checksum(release):
    release.add_asset("cpu", good_sum=False)
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=1" in result.stdout
    assert "Prüfsumme" in result.stderr
    assert not (release.data / "engine" / "cpu").exists()


def test_prebuilt_requires_a_checksum(release):
    release.add_asset("cpu", checksum=None)
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=1" in result.stdout
    assert "Keine Prüfsumme" in result.stderr
    assert not (release.data / "engine" / "cpu").exists()


@pytest.mark.parametrize("member", ["../evil", "/tmp/evil", "other/whisper-server"])
def test_prebuilt_rejects_archives_that_leave_the_engine_folder(release, member):
    release.add_asset("cpu", members={member: b"x", "cpu/whisper-server": b"#!/bin/sh\n"})
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=1" in result.stdout
    assert "unerwartete Pfade" in result.stderr
    assert not (release.data / "engine" / "cpu").exists()


def test_prebuilt_keeps_a_working_engine_when_the_release_has_no_package(release):
    old = release.data / "engine" / "cpu"
    old.mkdir(parents=True)
    (old / "whisper-server").write_text("#!/bin/sh\n")
    (old / "whisper-server").chmod(0o755)
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$? $SERVER_BIN_PATH"')
    assert f"rc=0 {old}/run-server.sh" in result.stdout, result.stderr
    assert "behalte die vorhandene Engine" in result.stderr
    assert not any("/download/" in url for url in release.downloads())


def test_prebuilt_skips_the_download_when_the_engine_matches_the_release(release):
    release.add_asset("cpu")
    release.publish()
    assert "rc=0" in release.run('install_prebuilt_engine; echo "rc=$?"').stdout
    release.fixtures.joinpath("curl.log").unlink()
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=0" in result.stdout
    assert release.downloads() == []


def test_prebuilt_replaces_an_engine_from_another_release(release):
    old = release.data / "engine" / "cpu"
    old.mkdir(parents=True)
    (old / "whisper-server").write_text("#!/bin/sh\necho old\n")
    (old / "whisper-server").chmod(0o755)
    (old / ".release").write_text("v1.0.0\n")
    release.add_asset("cpu")
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=0" in result.stdout, result.stderr
    assert (old / ".release").read_text().strip() == TAG
    assert "echo engine" in (old / "whisper-server").read_text()


def test_prebuilt_is_refused_on_other_architectures(release):
    _shim(release.bin, "uname", "echo aarch64\n")
    release.add_asset("cpu")
    release.publish()
    result = release.run('install_prebuilt_engine; echo "rc=$?"')
    assert "rc=1" in result.stdout
    assert "nur für x86_64" in result.stderr


def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def whisper_remote(tmp_path):
    """A local repository with two tagged commits standing in for whisper.cpp."""
    repo = tmp_path / "remote"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    commits = {}
    for tag in ("v1", "v2"):
        (repo / "file").write_text(tag)
        _git("add", "file", cwd=repo)
        _git("commit", "-q", "-m", tag, cwd=repo)
        _git("tag", "-a", tag, "-m", tag, cwd=repo)
        commits[tag] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
            capture_output=True, check=True).stdout.strip()
    return repo, commits


def _checkout(release, repo, tag, commit):
    return release.run(f'''
        WHISPER_CPP_URL="file://{repo}"; WHISPER_CPP_TAG="{tag}"
        WHISPER_CPP_COMMIT="{commit}"
        whisper_checkout "$DATA_DIR/whisper.cpp"; echo "rc=$?"
        git -C "$DATA_DIR/whisper.cpp" rev-parse HEAD
    ''')


def test_whisper_checkout_clones_the_pinned_tag(release, whisper_remote):
    repo, commits = whisper_remote
    result = _checkout(release, repo, "v1", commits["v1"])
    assert "rc=0" in result.stdout, result.stderr
    assert commits["v1"] in result.stdout


def test_whisper_checkout_moves_an_old_clone_to_the_pinned_tag(release, whisper_remote):
    repo, commits = whisper_remote
    assert "rc=0" in _checkout(release, repo, "v1", commits["v1"]).stdout
    result = _checkout(release, repo, "v2", commits["v2"])
    assert "rc=0" in result.stdout, result.stderr
    assert commits["v2"] in result.stdout


def test_whisper_checkout_refuses_a_tag_that_moved(release, whisper_remote):
    repo, commits = whisper_remote
    result = _checkout(release, repo, "v1", commits["v2"])
    assert "rc=1" in result.stdout
    assert "Abbruch" in result.stderr


def test_whisper_checkout_replaces_the_rest_of_an_aborted_clone(release, whisper_remote):
    repo, commits = whisper_remote
    (release.data / "whisper.cpp").mkdir()
    (release.data / "whisper.cpp" / "junk").write_text("x")
    result = _checkout(release, repo, "v1", commits["v1"])
    assert "rc=0" in result.stdout, result.stderr
    assert not (release.data / "whisper.cpp" / "junk").exists()


def test_whisper_pin_is_a_released_tag_with_a_full_commit():
    pin = PIN.read_text(encoding="utf-8")
    assert 'WHISPER_CPP_TAG="v' in pin
    commit = pin.split('WHISPER_CPP_COMMIT="', 1)[1].split('"', 1)[0]
    assert len(commit) == 40 and set(commit) <= set("0123456789abcdef")


def test_installer_builds_whisper_from_the_pin_and_rebuilds_on_change():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'whisper_checkout "$DATA/whisper.cpp"' in installer
    assert "git clone --depth 1 https://github.com/ggml-org/whisper.cpp" not in installer
    assert 'whisper_build_stamp "${CMAKE_FLAGS[*]}"' in installer
    assert '!= "$WANT_STAMP"' in installer


def test_installer_falls_back_from_prebuilt_to_source():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "if install_prebuilt_engine; then" in installer
    assert "releases/latest/download" not in installer
    assert "Engine-Download fehlgeschlagen" not in installer


def test_online_installer_passes_its_release_tag_to_install_sh():
    installer = (ROOT / "scripts" / "quassel-install.sh").read_text(
        encoding="utf-8")
    assert 'export QUASSEL_RELEASE_TAG="$TAG"' in installer
    assert installer.index("QUASSEL_RELEASE_TAG") < installer.index("exec bash")
    assert "vorgebaute Engine + ein" not in installer


def test_set_env_key_replaces_appends_and_keeps_other_lines(release):
    env = release.data / "server.env"
    env.write_text("SERVER_BIN=/x\nMODEL_PATH=/old\nMODEL_PATH=/older\nWHISPER_DECODE=-bs 1\n")
    result = release.run(f'''
        set_env_key "{env}" MODEL_PATH "/new dir/ggml-small.bin" && echo set
        set_env_key "{env}" VAD_MODEL '/a\\b&c' && echo added
    ''')
    assert "set" in result.stdout and "added" in result.stdout, result.stderr
    lines = env.read_text().splitlines()
    assert lines == ["SERVER_BIN=/x", "MODEL_PATH=/new dir/ggml-small.bin",
                     "WHISPER_DECODE=-bs 1", "VAD_MODEL=/a\\b&c"]
    assert not list(release.data.glob("server.env.*"))


def test_env_value_returns_the_last_entry(release):
    env = release.data / "server.env"
    env.write_text("MODEL_PATH=/one\nMODEL_PATH=/two\n")
    result = release.run(f'env_value "{env}" MODEL_PATH; env_value "{env}" NOPE')
    assert result.stdout == "/two\n"


def test_model_option_updates_an_existing_installation():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "MODEL_EXPLICIT=1" in installer
    assert 'if [[ "$MODEL_EXPLICIT" -eq 1 ]]; then' in installer
    assert 'set_env_key "$SENV" MODEL_PATH "$MODEL_FILE_PATH"' in installer
    assert "Ungültiger Modellname" in installer


def test_model_option_rejects_a_path_before_anything_is_installed(tmp_path):
    _need_posix_shell()
    shims = tmp_path / "bin"
    shims.mkdir()
    _shim(shims, "systemctl", "exit 0\n")
    result = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--model", "../../etc/passwd"],
        env={"PATH": f"{shims}:{os.environ['PATH']}", "HOME": str(tmp_path)},
        text=True, capture_output=True, check=False)
    assert result.returncode == 1
    assert "Ungültiger Modellname" in result.stderr
    assert "1/8" not in result.stdout


def test_engine_workflow_runs_only_for_releases_and_manual_dispatch():
    workflow = (ROOT / ".github" / "workflows" / "engine-packages.yml").read_text(
        encoding="utf-8")
    assert "  release:\n    types: [published]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "  push:" not in workflow and "pull_request" not in workflow
    # Only the built-in token; no other secret may be needed.
    secrets = {part.split("}}")[0].strip()
               for part in workflow.split("secrets.")[1:]}
    assert secrets == {"GITHUB_TOKEN"}
    assert "contents: write" in workflow
    assert workflow.count("contents: write") == 1


def test_engine_workflow_packs_what_the_installer_expects():
    workflow = (ROOT / ".github" / "workflows" / "engine-packages.yml").read_text(
        encoding="utf-8")
    # <variant>/ at the top of the archive, a .sha256 next to it, the names
    # install_prebuilt_engine asks for.
    assert 'asset="quassel-engine-linux-$VARIANT-x86_64.tar.gz"' in workflow
    assert 'tar -C out -czf "$asset" "$VARIANT"' in workflow
    assert 'sha256sum "$asset" > "$asset.sha256"' in workflow
    assert "build-engine-cpu.sh" in workflow and "build-engine-cuda.sh" in workflow
    library = LIB.read_text(encoding="utf-8")
    assert 'asset="quassel-engine-linux-$evar-x86_64.tar.gz"' in library
    # The tag reaches shell code only through env, never by interpolation.
    assert "TAG: ${{ github.event.release.tag_name || inputs.tag }}" in workflow
    assert 'gh release upload "$TAG"' in workflow


def test_engine_build_scripts_use_the_pinned_whisper_cpp():
    for name in ("build-engine-cpu.sh", "build-engine-cuda.sh"):
        script = (ROOT / "scripts" / "offline" / name).read_text(encoding="utf-8")
        assert 'source "$HERE/../whisper-cpp-pin.sh"' in script
        assert '--branch "$WHISPER_CPP_TAG"' in script
        assert "git clone --depth 1 https://github.com/ggml-org/whisper.cpp" not in script
        assert "$WHISPER_CPP_COMMIT" in script


def _pinned_tag():
    pin = PIN.read_text(encoding="utf-8")
    return pin.split('WHISPER_CPP_TAG="', 1)[1].split('"', 1)[0]


def test_docs_name_the_pinned_whisper_cpp_version():
    for name in ("README.md", "INSTALL.md"):
        assert _pinned_tag() in (ROOT / name).read_text(encoding="utf-8"), name


def test_docs_do_not_send_users_to_offline_packages_missing_from_latest():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    site = (ROOT / "website" / "index.html").read_text(encoding="utf-8")
    for text in (readme, site):
        assert "latest/download/quassel-engine" not in text
        assert "latest/download/quassel-linux" not in text
        assert "latest/download/Quassel-Offline" not in text
    # The offline packages are linked through the tag that has them.
    assert readme.count("releases/tag/v2.2.0") >= 2
    assert "releases/latest) — it" not in readme
    # The old claim that the quick installer never compiles is wrong now.
    assert "no compiling" not in site
    assert "v2.6.0" not in site


def test_website_offline_packages_are_labelled_as_v2_2_0_only():
    site = (ROOT / "website" / "index.html").read_text(encoding="utf-8")
    assert site.count("Only available as v2.2.0") >= 3
    assert "releases/latest/download/quassel-install.sh" in site


# --------------------------------------------------------------------------
# AUR draft: no makepkg here, so source the PKGBUILD and run its functions
# --------------------------------------------------------------------------
PKGBUILD = ROOT / "packaging" / "aur" / "PKGBUILD"

# macOS has no `install -D`; this stands in for GNU install in the tests.
INSTALL_SHIM = textwrap.dedent("""\
    mode=""; dirs=0; mk=0; args=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -Dm*) mk=1; mode="${1#-Dm}"; shift ;;
            -m) mode="$2"; shift 2 ;;
            -m*) mode="${1#-m}"; shift ;;
            -D) mk=1; shift ;;
            -d) dirs=1; shift ;;
            *) args+=("$1"); shift ;;
        esac
    done
    if [[ $dirs -eq 1 ]]; then mkdir -p "${args[@]}"; exit 0; fi
    dest="${args[${#args[@]}-1]}"
    srcs=("${args[@]:0:${#args[@]}-1}")
    if [[ $mk -eq 1 ]]; then mkdir -p "$(dirname "$dest")"; fi
    for s in "${srcs[@]}"; do
        target="$dest"; [[ -d "$dest" ]] && target="$dest/$(basename "$s")"
        if [[ "$s" == /dev/stdin ]]; then cat > "$target"; else cp "$s" "$target"; fi
        [[ -n "$mode" ]] && chmod "$mode" "$target"
    done
""")


def _run_pkgbuild(tmp_path, body, srcdir=None):
    _need_posix_shell()
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    _shim(shims, "install", INSTALL_SHIM)
    # BSD sed wants a suffix argument after -i; makepkg runs GNU sed.
    _shim(shims, "sed", 'if [[ "${1:-}" == "-i" ]] && ! /usr/bin/sed --version '
          '>/dev/null 2>&1; then shift; exec /usr/bin/sed -i "" "$@"; fi\n'
          'exec /usr/bin/sed "$@"\n')
    script = (f'set -euo pipefail\nsource "{PKGBUILD}"\n'
              f'srcdir="{srcdir or tmp_path / "src"}"\n'
              f'pkgdir="{tmp_path / "pkg"}"\n') + textwrap.dedent(body)
    return subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, text=True, capture_output=True,
        env={"PATH": f"{shims}:{os.environ['PATH']}", "HOME": str(tmp_path)},
        check=False)


def test_pkgbuild_has_the_fields_makepkg_and_namcap_expect():
    _need_posix_shell()
    text = PKGBUILD.read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(PKGBUILD)]).returncode == 0
    result = subprocess.run(
        ["bash", "-c", f'source "{PKGBUILD}"; '
         'printf "%s\\n" "$pkgname" "$pkgver" "${arch[*]}" "${#source[@]}" '
         '"${#sha256sums[@]}" "$install" "${provides[*]}"; '
         'declare -F build package pkgver | wc -l'],
        text=True, capture_output=True, check=True)
    name, ver, arch, nsrc, nsum, install, provides, funcs = result.stdout.split("\n")[:8]
    assert name == "quassel-type-git"          # quassel-git is the IRC client
    assert ver[0].isdigit() and "-" not in ver
    assert arch == "x86_64"                    # a compiled binary is inside
    assert nsrc == nsum == "2"
    assert install == "quassel.install" and provides == "quassel-type"
    assert funcs.strip() == "3"
    assert (ROOT / "packaging" / "aur" / install).exists()
    assert "2.1.0" not in text


def test_pkgbuild_builds_the_same_whisper_cpp_as_the_installer():
    text = PKGBUILD.read_text(encoding="utf-8")
    assert f"_whisper={_pinned_tag().lstrip('v')}\n" in text
    assert "refs/tags/v$_whisper.tar.gz" in text
    assert "-DWHISPER_BUILD_SERVER=ON" in text
    assert "--target whisper-server" in text
    # No package of that name exists in the AUR, so it must not be a dependency.
    depends = text.split("depends=(", 1)[1].split(")", 1)[0]
    assert "whisper.cpp" not in depends


def test_pkgbuild_pkgver_comes_from_tags_with_a_commit_fallback(tmp_path):
    _need_posix_shell()
    repo = tmp_path / "src" / "quassel"
    repo.mkdir(parents=True)
    _git("init", "-q", cwd=repo)
    (repo / "f").write_text("1")
    _git("add", "f", cwd=repo)
    _git("commit", "-q", "-m", "one", cwd=repo)
    untagged = _run_pkgbuild(tmp_path, "pkgver")
    assert untagged.returncode == 0, untagged.stderr
    assert untagged.stdout.strip().startswith("r1.")
    _git("tag", "-a", "v2.8.0", "-m", "v", cwd=repo)
    (repo / "f").write_text("2")
    _git("commit", "-q", "-am", "two", cwd=repo)
    tagged = _run_pkgbuild(tmp_path, "pkgver")
    assert tagged.returncode == 0, tagged.stderr
    assert tagged.stdout.strip().startswith("2.8.0.r1.g")


def test_pkgbuild_package_installs_every_launcher_unit_and_icon(tmp_path):
    _need_posix_shell()
    src = tmp_path / "src" / "quassel"
    for name in ("bin", "systemd", "desktop", "assets", "quassel"):
        shutil.copytree(ROOT / name, src / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("LICENSE", "ADDITIONAL-PERMISSIONS.md"):
        shutil.copy(ROOT / name, src / name)
    engine = tmp_path / "src" / "build-whisper" / "bin"
    engine.mkdir(parents=True)
    (engine / "whisper-server").write_text("#!/bin/sh\n")
    result = _run_pkgbuild(tmp_path, "cd \"$srcdir\"; package")
    assert result.returncode == 0, result.stderr
    pkg = tmp_path / "pkg"

    for launcher in ("quasseld", "quassel-type", "quassel-pill", "quassel-ctl",
                     "quassel-python", "quassel-type-cli"):
        text = (pkg / "usr" / "bin" / launcher).read_text(encoding="utf-8")
        assert ".local/lib/quassel" not in text
    assert "/usr/lib/quassel" in (pkg / "usr/bin/quasseld").read_text(encoding="utf-8")
    assert not (pkg / "usr" / "bin" / "quassel").exists()      # the old typo
    assert (pkg / "usr/lib/quassel/quassel/daemon.py").exists()
    assert (pkg / "usr/lib/quassel/assets/quassel.svg").exists()
    assert os.access(pkg / "usr/lib/quassel/engine/whisper-server", os.X_OK)

    units = {p.name: p.read_text(encoding="utf-8")
             for p in (pkg / "usr/lib/systemd/user").glob("*.service")}
    assert set(units) >= {"quasseld.service", "quassel-server.service",
                          "quassel-server@.service"}
    # Programmpfade gehören ins Paket; Nutzerdaten (%h/.local/share) bleiben im Home.
    assert all("%h/.local/bin" not in text and "%h/.local/lib" not in text
               for text in units.values())
    assert "ExecStart=/usr/bin/quasseld" in units["quasseld.service"]
    assert "${SERVER_BIN:-/usr/lib/quassel/engine/whisper-server}" \
        in units["quassel-server.service"]
    assert "${SERVER_BIN:-/usr/lib/quassel/engine/whisper-server}" \
        in units["quassel-server@.service"]

    desktop = (pkg / "usr/share/applications/quassel.desktop").read_text(
        encoding="utf-8")
    assert "@HOME@" not in desktop and "/.local/" not in desktop
    assert "Exec=quassel-type\n" in desktop
    assert "Icon=quassel-voice\n" in desktop
    icons = pkg / "usr/share/icons/hicolor"
    assert (icons / "scalable/apps/quassel-voice.svg").exists()
    assert (icons / "128x128/apps/quassel-voice.png").exists()
    assert not (icons / "scalable/apps/quassel.svg").exists()   # IRC client's name
    assert "uinput" in (pkg / "usr/lib/udev/rules.d/80-quassel-uinput.rules").read_text()
    assert (pkg / "usr/lib/modules-load.d/quassel-uinput.conf").read_text().strip() == "uinput"
    assert (pkg / "usr/share/licenses/quassel-type-git/LICENSE").exists()
    assert not list(pkg.rglob(".tmp-*")) and not list(pkg.rglob(".quassel.desktop"))


def test_cask_comment_no_longer_waits_for_the_ffmpeg_measurement():
    cask = (ROOT / "packaging" / "homebrew" / "Casks" / "quassel.rb").read_text(
        encoding="utf-8")
    assert "solange die Messung läuft" not in cask
    assert "seit 2.5.0" in cask
    assert '\n  depends_on formula: "ffmpeg"' not in cask
