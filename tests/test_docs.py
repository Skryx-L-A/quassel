"""The reference pages must keep up with the code: every setting that the code
reads is listed in docs/CONFIG.md, and no document links to a file that is not
there. Pure text checks, no I/O beyond reading the repository."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "INSTALL.md", ROOT / "ROADMAP.md",
        *sorted((ROOT / "docs").glob("*.md")),
        *sorted((ROOT / "docs" / "history").glob("*.md"))]


def _documented_keys():
    """{section: {key, ...}} from the tables under '### `[section]`' headings."""
    text = (ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8")
    out, section = {}, None
    for line in text.splitlines():
        heading = re.match(r"^###? `\[(\w+)\]`", line)
        if heading:
            section = heading.group(1)
            out.setdefault(section, set())
            continue
        if line.startswith("#"):
            section = None
            continue
        row = re.match(r"^\| ((?:`\w+`(?:, )?)+) \|", line)
        if section and row:
            out[section].update(re.findall(r"`(\w+)`", row.group(1)))
    return out


def _keys_read_by_config():
    source = (ROOT / "quassel" / "config.py").read_text(encoding="utf-8")
    pairs = set(re.findall(r'_get\w+\(p, "(\w+)", "(\w+)"', source))
    pairs |= set(re.findall(r'\bg\("(\w+)", "(\w+)"', source))
    return pairs


def test_every_key_read_by_config_py_is_documented():
    documented = _documented_keys()
    assert len(_keys_read_by_config()) >= 35      # the patterns above still match
    missing = sorted("[%s] %s" % pair for pair in _keys_read_by_config()
                     if pair[1] not in documented.get(pair[0], set()))
    assert not missing, "not in docs/CONFIG.md: " + ", ".join(missing)


def test_config_md_does_not_list_keys_the_code_never_reads():
    read = _keys_read_by_config()
    # Sections whose keys are read outside config.py.
    elsewhere = {("meta", "version"), ("vad", "backend"), ("vad", "silero_model"),
                 ("vad", "autostop"), ("vad", "autostop_silence"),
                 ("wakeword", "spotter_model"), ("wakeword", "spotter_threshold"),
                 ("wakeword", "verify"), ("mcp", "mcp_history"), ("stats", "typing_wpm")}
    extra = sorted("[%s] %s" % (sec, key)
                   for sec, keys in _documented_keys().items() for key in keys
                   if (sec, key) not in read and (sec, key) not in elsewhere)
    assert not extra, "documented but not read by the code: " + ", ".join(extra)


def test_vad_wakeword_and_mcp_keys_exist_in_their_modules():
    vad = (ROOT / "quassel" / "vad.py").read_text(encoding="utf-8")
    wake = (ROOT / "quassel" / "wakeword.py").read_text(encoding="utf-8")
    mcp = (ROOT / "quassel" / "mcp_server.py").read_text(encoding="utf-8")
    documented = _documented_keys()
    for key in documented["vad"]:
        assert '"%s"' % key in vad, key
    for key in ("spotter_model", "spotter_threshold", "verify"):
        assert key in documented["wakeword"] and '"%s"' % key in wake, key
    assert "mcp_history" in documented["mcp"] and '"mcp_history"' in mcp
    stats = (ROOT / "quassel" / "stats.py").read_text(encoding="utf-8")
    assert "typing_wpm" in documented["stats"] and '"typing_wpm"' in stats


def test_profile_keys_documented():
    source = (ROOT / "quassel" / "profiles.py").read_text(encoding="utf-8")
    text = (ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8")
    section = text.split("## `profiles.ini`", 1)[1].split("## `server.env`", 1)[0]
    for key in ("match", "language", "ai", "ai_mode", "programmer_mode",
                "punctuation", "allow_enter", "enabled"):
        assert '"%s"' % key in source, key
        assert "| `%s` |" % key in section, key


def test_relative_links_in_the_documents_resolve():
    broken = []
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if re.match(r"^(https?:|mailto:|#)", target):
                continue
            path = (doc.parent / target.split("#", 1)[0]).resolve()
            if not path.exists():
                broken.append("%s -> %s" % (doc.relative_to(ROOT), target))
    assert not broken, broken


def test_historical_checklists_are_marked_and_out_of_the_repository_root():
    assert not (ROOT / "WINDOWS_VERIFY.md").exists()
    assert not (ROOT / "WINDOWS_VERIFY_AI.md").exists()
    for name in ("WINDOWS_VERIFY.md", "WINDOWS_VERIFY_AI.md"):
        text = (ROOT / "docs" / "history" / name).read_text(encoding="utf-8")
        assert "Historical document" in text


def test_bug_template_covers_every_platform():
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md").read_text(
        encoding="utf-8")
    for needle in ("Version", "journalctl", "daemon.log", "debug.log", "crash.log"):
        assert needle in text, needle
