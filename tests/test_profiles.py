"""Audit G102: Per-App-Profile — Datei, Treffer, Überlagerung und ihre
Wirkung im Diktatpfad. appctx ist überall eine Attrappe; kein Fenster, kein
Mikrofon, kein Server."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from quassel import appctx, dictation, i18n, profiles

TERMINAL = appctx.make("com.apple.Terminal", "Terminal", "Terminal")
KITTY = appctx.make(process="kitty", wm_class="kitty")
VSCODE_WIN = appctx.make(process=r"C:\Programs\Code.exe", wm_class="Chrome_WidgetWin_1")
MAIL = appctx.make("com.apple.mail", "Mail", "Mail")
BROWSER = appctx.make("org.mozilla.firefox", "firefox", "Firefox")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Nie die echte profiles.ini des Nutzers lesen."""
    monkeypatch.setattr(profiles, "PROFILES", str(tmp_path / "profiles.ini"))
    profiles._cache["key"] = None
    yield
    profiles._cache["key"] = None


def write(tmp_path, text):
    path = tmp_path / "profiles.ini"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------- parse
def test_parse_reads_all_settings():
    [p] = profiles.parse("""
[profile:Code]
match = code*, com.microsoft.VSCode
language = EN
ai = off
ai_mode = cleanup
programmer_mode = on
punctuation = aus
allow_enter = no
""")
    assert p.name == "Code"
    assert p.patterns == ("code*", "com.microsoft.vscode")
    assert p.settings == {"language": "en", "ai": False, "ai_mode": "cleanup",
                          "programmer_mode": True, "punctuation": False,
                          "allow_enter": False}


def test_parse_skips_invalid_and_disabled():
    out = profiles.parse("""
[general]
x = 1
[profile:NoMatch]
language = de
[profile:Off]
match = kitty
enabled = off
[profile:Bad]
match = foo
language = ../etc
programmer_mode = vielleicht
ai_mode = off
""")
    assert [p.name for p in out] == ["Bad"]
    assert out[0].settings == {"ai_mode": None}
    assert profiles.parse("kaputt [") == []


def test_defaults_parse_and_cover_terminals_ides_mail():
    out = profiles.parse(profiles.DEFAULTS)
    assert [p.name for p in out] == ["Terminal", "IDE", "Mail"]
    term = profiles.find(TERMINAL, out)
    assert term.name == "Terminal" and term.allow_enter is False
    assert term.settings["programmer_mode"] is True
    assert profiles.find(KITTY, out).name == "Terminal"
    assert profiles.find(appctx.make(process="WindowsTerminal.exe"), out).name == "Terminal"
    assert profiles.find(appctx.make(wm_class="gnome-terminal"), out).name == "Terminal"
    assert profiles.find(VSCODE_WIN, out).name == "IDE"
    assert profiles.find(appctx.make(wm_class="jetbrains-pycharm"), out).name == "IDE"
    assert profiles.find(MAIL, out).name == "Mail"
    assert profiles.find(BROWSER, out) is None


# -------------------------------------------------------------- find/match
def test_first_matching_profile_wins_and_is_case_insensitive():
    out = profiles.parse("""
[profile:A]
match = KIT?Y
language = de
[profile:B]
match = kitty
language = en
""")
    assert profiles.find(KITTY, out).name == "A"


def test_unknown_app_never_matches_wildcards():
    out = profiles.parse("[profile:All]\nmatch = *\nlanguage = de\n")
    assert profiles.find(appctx.UNKNOWN, out) is None
    assert profiles.find(None, out) is None
    assert profiles.find(BROWSER, out).name == "All"


# -------------------------------------------------------------- load/write
def test_missing_file_means_no_profiles(tmp_path):
    assert profiles.load() == []
    assert not profiles.exists()


def test_load_rereads_after_change(tmp_path):
    path = write(tmp_path, "[profile:A]\nmatch = kitty\nlanguage = de\n")
    assert profiles.load(path)[0].settings["language"] == "de"
    with open(path, "w", encoding="utf-8") as f:
        f.write("[profile:A]\nmatch = kitty\nlanguage = en\n# länger\n")
    assert profiles.load(path)[0].settings["language"] == "en"


def test_write_defaults_is_atomic_and_never_overwrites(tmp_path, monkeypatch):
    path = str(tmp_path / "sub" / "profiles.ini")
    calls = []
    real = profiles.fsutil.atomic_write
    monkeypatch.setattr(profiles.fsutil, "atomic_write",
                        lambda p, t: calls.append(p) or real(p, t))
    assert profiles.write_defaults(path) is True
    assert calls == [path]
    assert open(path, encoding="utf-8").read() == profiles.DEFAULTS
    with open(path, "w", encoding="utf-8") as f:
        f.write("mine")
    assert profiles.write_defaults(path) is False
    assert open(path, encoding="utf-8").read() == "mine"


def test_profile_path_sits_next_to_config():
    assert os.path.dirname(profiles.config.CONFIG) == profiles.config.CONFDIR
    assert os.path.basename(profiles.PROFILES) == "profiles.ini"


# ----------------------------------------------------------------- overlay
def base_cfg(**kw):
    values = dict(language="auto", punctuation=True, commands=True,
                  programmer_mode=False, ai_enabled=True, ai_post_process=False,
                  ai_post_mode="cleanup", ai_voice_modes=True, mic="default")
    values.update(kw)
    return SimpleNamespace(**values)


def test_overlay_changes_only_the_layer():
    cfg = base_cfg()
    [p] = profiles.parse("""[profile:T]
match = kitty
language = de
programmer_mode = on
punctuation = off
ai_mode = email
allow_enter = off
""")
    layer = profiles.overlay(cfg, p)
    assert (layer.language, layer.programmer_mode, layer.punctuation) == ("de", True, False)
    assert (layer.ai_post_process, layer.ai_post_mode) == (True, "email")
    assert layer.mic == "default"                    # nicht genannt -> Basis
    assert profiles.allow_enter(layer) is False
    layer.ai_modes_text = "x"                        # Zuweisung bleibt oben
    assert cfg == base_cfg()                         # Basis unberührt
    assert profiles.allow_enter(cfg) is True


def test_overlay_ai_off_and_mode_off():
    [p] = profiles.parse("[profile:X]\nmatch = x\nai = off\nai_mode = off\n")
    layer = profiles.overlay(base_cfg(ai_post_process=True), p)
    assert layer.ai_enabled is False and layer.ai_post_process is False


def test_overlay_without_profile_is_the_base():
    cfg = base_cfg()
    assert profiles.overlay(cfg, None) is cfg


def test_without_file_the_app_is_not_even_queried():
    cfg = base_cfg()

    def never():
        raise AssertionError("appctx must not be asked without profiles.ini")
    assert profiles.for_active_app(cfg, active_app=never) is cfg


def test_for_active_app_logs_name_and_id_only(tmp_path):
    path = write(tmp_path, profiles.DEFAULTS)
    logs = []
    layer = profiles.for_active_app(base_cfg(), active_app=lambda: TERMINAL,
                                    path=path, log=logs.append)
    assert layer.programmer_mode is True
    assert logs == ["profile: Terminal (com.apple.terminal)"]


def test_for_active_app_survives_a_failing_query(tmp_path):
    path = write(tmp_path, profiles.DEFAULTS)
    cfg = base_cfg()

    def boom():
        raise RuntimeError("x")
    assert profiles.for_active_app(cfg, active_app=boom, path=path) is cfg
    assert profiles.for_active_app(cfg, active_app=lambda: appctx.UNKNOWN, path=path) is cfg


def test_strip_trailing_enter():
    assert profiles.strip_trailing_enter("ls -la\n") == "ls -la"
    assert profiles.strip_trailing_enter("a\nb\r\n \n") == "a\nb"
    assert profiles.strip_trailing_enter("a\nb") == "a\nb"
    assert profiles.strip_trailing_enter("echo hi ") == "echo hi "


# ----------------------------------------------------------- Diktatpfad
from test_dictation_privacy import setup  # noqa: E402


def run_dictation(monkeypatch, tmp_path, app, transcript="hallo welt", ini=None,
                  postprocess=None, **cfg):
    if ini is not None:
        write(tmp_path, ini)
    monkeypatch.setattr(appctx, "active_app", lambda: app)
    core = setup(monkeypatch, tmp_path, transcript=transcript)
    if postprocess is not None:
        monkeypatch.setattr(dictation.textproc, "postprocess", postprocess)
    for k, v in cfg.items():
        setattr(core.cfg, k, v)
    seen = {}
    real = dictation.whisperclient.transcribe_result

    def spy(wav, c, **kw):
        seen["language"] = getattr(c, "language", None)
        return real(wav, c, **kw)
    monkeypatch.setattr(dictation.whisperclient, "transcribe_result", spy)
    core.entered = 0
    core._send_enter = lambda: setattr(core, "entered", core.entered + 1)
    core._send_backspaces = lambda n: None
    core.cfg.language = "auto"
    assert core.start_recording()
    core.finish_now()
    return core, seen


TERM_INI = """[profile:Terminal]
match = kitty
allow_enter = off
programmer_mode = on
language = en
"""


def test_without_profiles_file_nothing_changes(monkeypatch, tmp_path):
    def never():
        raise AssertionError("no query without profiles.ini")
    monkeypatch.setattr(appctx, "active_app", never)
    core = setup(monkeypatch, tmp_path, transcript="ls\n")
    assert core.start_recording()
    core.finish_now()
    assert core.pasted == ["ls\n"]


def test_terminal_profile_strips_the_trailing_enter(monkeypatch, tmp_path):
    core, _ = run_dictation(monkeypatch, tmp_path, KITTY, transcript="ls -la\n",
                            ini=TERM_INI)
    assert core.pasted == ["ls -la"]
    assert core.cfg.programmer_mode is False         # gespeicherte Cfg unberührt


def test_terminal_profile_blocks_a_spoken_enter(monkeypatch, tmp_path):
    core, _ = run_dictation(monkeypatch, tmp_path, KITTY, ini=TERM_INI,
                            postprocess=lambda raw, cfg: ("command", "enter"))
    assert core.entered == 0 and core.pasted == []
    assert core.states[-1] == ("error", i18n.tr("enter_blocked"))


def test_enter_blocked_has_both_languages(monkeypatch):
    monkeypatch.setattr(i18n, "current", lambda: "de")
    assert i18n.tr("enter_blocked") == "Enter in dieser App gesperrt"
    monkeypatch.setattr(i18n, "current", lambda: "en")
    assert i18n.tr("enter_blocked") == "Enter is blocked in this app"


def test_other_apps_still_get_enter(monkeypatch, tmp_path):
    core, _ = run_dictation(monkeypatch, tmp_path, BROWSER, ini=TERM_INI,
                            postprocess=lambda raw, cfg: ("command", "enter"))
    assert core.entered == 1


def test_profile_language_reaches_transcription_and_textproc(monkeypatch, tmp_path):
    got = {}

    def post(raw, cfg):
        got["language"] = cfg.language
        return "text", raw
    _, seen = run_dictation(monkeypatch, tmp_path, KITTY, ini=TERM_INI, postprocess=post)
    assert seen["language"] == "en"
    assert got["language"] == "en"


def test_profile_programmer_mode_applies_for_this_dictation_only(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(dictation.progmode, "apply_if_addressed",
                        lambda t: calls.append(t) or t)
    run_dictation(monkeypatch, tmp_path, KITTY, ini=TERM_INI)
    assert calls == ["hallo welt"]
    calls.clear()
    run_dictation(monkeypatch, tmp_path, BROWSER, ini=TERM_INI)
    assert calls == []


def test_profile_ai_mode_and_ai_off(monkeypatch, tmp_path):
    modes = []
    monkeypatch.setattr(dictation.aimodes, "transform",
                        lambda text, mode, cfg: modes.append(mode) or text.upper())
    monkeypatch.setattr(dictation.config, "ai_modes_text", lambda: "")
    ini = """[profile:Mail]
match = com.apple.mail
ai_mode = email
[profile:Quiet]
match = kitty
ai = off
"""
    core, _ = run_dictation(monkeypatch, tmp_path, MAIL, ini=ini, ai_enabled=True,
                            ai_voice_modes=False, ai_post_process=False,
                            ai_post_mode="cleanup")
    assert modes == ["email"]
    assert core.pasted == ["HALLO WELT"]     # B19: einmal einfügen
    modes.clear()
    core, _ = run_dictation(monkeypatch, tmp_path, KITTY, ini=ini, ai_enabled=True,
                            ai_voice_modes=False, ai_post_process=True,
                            ai_post_mode="cleanup")
    assert modes == [] and core.pasted == ["hallo welt"]


def test_profile_layer_is_cleared_after_the_dictation(monkeypatch, tmp_path):
    core, _ = run_dictation(monkeypatch, tmp_path, KITTY, ini=TERM_INI)
    assert core._cfg_now() is core.cfg


def test_wake_insert_uses_the_profile_too(monkeypatch, tmp_path):
    write(tmp_path, TERM_INI)
    monkeypatch.setattr(appctx, "active_app", lambda: KITTY)
    core = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(dictation.textproc, "postprocess",
                        lambda raw, cfg: ("text", "make test\n"))
    core._wake_insert("make test")
    assert core.pasted == ["make test"]
    assert core._cfg_now() is core.cfg


def test_explicit_profile_programmer_mode_needs_no_code_prefix(monkeypatch, tmp_path):
    """OPEN 6: Profil mit programmer_mode = on formt ohne „Code:“."""
    core, _ = run_dictation(monkeypatch, tmp_path, KITTY, ini=TERM_INI,
                            transcript="x gleich eins")
    assert core.pasted == [dictation.progmode.apply("x gleich eins")]
    assert core.pasted != ["x gleich eins"]


def test_global_programmer_mode_still_needs_the_prefix(monkeypatch, tmp_path):
    core, _ = run_dictation(monkeypatch, tmp_path, BROWSER, ini=TERM_INI,
                            transcript="x gleich eins", programmer_mode=True)
    assert core.pasted == ["x gleich eins"]
    core, _ = run_dictation(monkeypatch, tmp_path, BROWSER, ini=TERM_INI,
                            transcript="Code: x gleich eins", programmer_mode=True)
    assert core.pasted == [dictation.progmode.apply("x gleich eins")]


def test_profile_programmer_mode_with_prefix_is_applied_once(monkeypatch, tmp_path):
    core, _ = run_dictation(monkeypatch, tmp_path, KITTY, ini=TERM_INI,
                            transcript="Code: x gleich eins")
    assert core.pasted == [dictation.progmode.apply("x gleich eins")]


def test_inherited_programmer_mode_is_not_explicit(tmp_path):
    [p] = profiles.parse("[profile:L]\nmatch = x\nlanguage = de\n")
    layer = profiles.overlay(base_cfg(programmer_mode=True), p)
    assert profiles.programmer_mode_explicit(layer) is False
    assert profiles.programmer_mode_explicit(base_cfg(programmer_mode=True)) is False


def streaming_core(monkeypatch, tmp_path, app, ini=TERM_INI):
    write(tmp_path, ini)
    monkeypatch.setattr(appctx, "active_app", lambda: app)
    core = setup(monkeypatch, tmp_path)
    core.cfg.streaming, core.cfg.streaming_mode = True, "word"
    core.streamer = None
    core._streaming_begin = lambda: None
    core.typed_keys = []
    core._type_chunk = core.typed_keys.append
    core._send_backspaces = lambda n: None
    core.enable_streaming()
    return core


def test_streaming_types_no_line_break_where_enter_is_blocked(monkeypatch, tmp_path):
    """OPEN 2: Streaming tippt Tasten; ein Umbruch wäre im Terminal ein Enter."""
    core = streaming_core(monkeypatch, tmp_path, KITTY)
    typed = core.streamer.finish("cd /tmp\nrm -rf x")
    keys = "".join(core.typed_keys)
    assert "\n" not in keys and "\r" not in keys
    assert len(keys) == len(typed)                  # Backspace-Abgleich stimmt


def test_streaming_keeps_line_breaks_elsewhere(monkeypatch, tmp_path):
    core = streaming_core(monkeypatch, tmp_path, BROWSER)
    core.streamer.finish("Hallo\nWelt")
    assert "\n" in "".join(core.typed_keys)


def test_prewarm_only_with_a_profiles_file(monkeypatch, tmp_path):
    """OPEN 7: AppKit nur vorwärmen, wenn es Profile gibt."""
    calls = []
    monkeypatch.setattr(appctx, "prewarm", lambda: calls.append(1) or "thread")
    assert profiles.prewarm() is None and calls == []
    write(tmp_path, profiles.DEFAULTS)
    assert profiles.prewarm() == "thread" and calls == [1]


def test_appctx_prewarm_loads_appkit_in_the_background(monkeypatch):
    loaded = []
    monkeypatch.setattr(appctx, "_workspace", lambda: loaded.append(1))
    monkeypatch.setattr(appctx.sys, "platform", "darwin")
    t = appctx.prewarm()
    t.join(2)
    assert t.daemon and loaded == [1]
    monkeypatch.setattr(appctx.sys, "platform", "linux")
    assert appctx.prewarm() is None


def test_daemon_prewarms_at_start(monkeypatch):
    from quassel import daemon
    calls = []
    monkeypatch.setattr(daemon.profiles, "prewarm", lambda: calls.append(1))
    monkeypatch.setattr(daemon.config, "Cfg", lambda: SimpleNamespace(
        ui_language="auto", beep_output="system"))
    monkeypatch.setattr(daemon.beep, "set_output", lambda n: None)
    monkeypatch.setattr(daemon, "Recorder", lambda **kw: None)
    monkeypatch.setattr(daemon, "AudioDucker", lambda: None)
    daemon.Daemon()
    assert calls == [1]


def run_cli(*args):
    lines = []
    code = profiles.cli(list(args), out=lines.append)
    return code, lines


def test_cli_path_init_list(tmp_path):
    """OPEN 5: `quassel profiles init|list|path`."""
    assert run_cli("path") == (0, [profiles.PROFILES])
    code, lines = run_cli("list")
    assert code == 0 and "quassel profiles init" in lines[0]
    assert run_cli("init") == (0, ["created " + profiles.PROFILES])
    code, lines = run_cli("init")
    assert code == 1 and "left unchanged" in lines[0]
    code, lines = run_cli("list")
    assert code == 0 and [l.split(":")[0] for l in lines] == ["Terminal", "IDE", "Mail"]
    assert "allow_enter=off" in lines[0] and "programmer_mode=on" in lines[0]


def test_cli_rejects_unknown_calls():
    for args in ((), ("edit",), ("list", "x")):
        assert run_cli(*args) == (2, [profiles.CLI_USAGE])


def test_cli_list_with_broken_file(tmp_path):
    write(tmp_path, "[profile:X]\nlanguage = de\n")
    code, lines = run_cli("list")
    assert code == 0 and lines[0].startswith("no valid profiles")
