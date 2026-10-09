"""UI-Sprache: Erkennung je Plattform und Vollständigkeit der Texttabelle.

Kein Test fragt die echte Systemsprache ab: die Betriebssystem-Abfrage wird
ersetzt, die Umgebungsvariablen kommen aus monkeypatch."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from quassel import i18n


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    i18n.set_language(None)
    i18n._os_ui_languages.cache_clear()
    yield
    i18n.set_language(None)
    i18n._os_ui_languages.cache_clear()


def _os_langs(monkeypatch, langs):
    monkeypatch.setattr(i18n, "_query_os_ui_languages", lambda: list(langs))
    i18n._os_ui_languages.cache_clear()


# ------------------------------------------------------------ I125 Erkennung
def test_finder_start_without_lang_follows_macos_preferred_languages(monkeypatch):
    """Aus dem Finder gestartet gibt es kein LANG. Die Oberfläche muss trotzdem
    der in macOS eingestellten Sprache folgen."""
    _os_langs(monkeypatch, ["de-DE", "en-US"])
    assert i18n.current() == "de"
    assert i18n.tr("turn_off") == "Ausschalten"


def test_os_language_wins_over_lang(monkeypatch):
    """Terminal setzt LANG oft aus der Region (en_US), obwohl die Systemsprache
    Deutsch ist; maßgeblich ist die Sprache der Systemeinstellungen."""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    _os_langs(monkeypatch, ["de-DE"])
    assert i18n.current() == "de"


def test_first_supported_os_language_is_used(monkeypatch):
    _os_langs(monkeypatch, ["fr-FR", "de-CH", "en-GB"])
    assert i18n.current() == "de"
    _os_langs(monkeypatch, ["ja-JP", "en-GB", "de-DE"])
    assert i18n.current() == "en"


def test_lang_is_fallback_when_os_reports_nothing(monkeypatch):
    _os_langs(monkeypatch, [])
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert i18n.current() == "de"


def test_lc_all_still_overrides_everything(monkeypatch):
    """LC_ALL ist die ausdrückliche Übersteuerung (z.B. für einen Test-Start)."""
    _os_langs(monkeypatch, ["de-DE"])
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    assert i18n.current() == "en"


def test_lc_messages_counts_like_posix(monkeypatch):
    _os_langs(monkeypatch, [])
    monkeypatch.setenv("LC_MESSAGES", "de_AT.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.current() == "de"


def test_explicit_setting_beats_detection(monkeypatch):
    _os_langs(monkeypatch, ["de-DE"])
    i18n.set_language("en")
    assert i18n.current() == "en"


def test_os_query_runs_once(monkeypatch):
    """tr() läuft bei jedem Neuzeichnen; die Systemabfrage darf nicht jedes Mal
    einen Prozess starten."""
    calls = []

    def query():
        calls.append(1)
        return ["de-DE"]
    monkeypatch.setattr(i18n, "_query_os_ui_languages", query)
    i18n._os_ui_languages.cache_clear()
    for _ in range(5):
        i18n.current()
    assert len(calls) == 1


def test_os_query_failure_falls_back(monkeypatch):
    def broken():
        raise OSError("kein defaults")
    monkeypatch.setattr(i18n, "_query_os_ui_languages", broken)
    i18n._os_ui_languages.cache_clear()
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert i18n.current() == "de"


def test_parse_defaults_output():
    out = '(\n    "de-DE",\n    "en-US",\n    fr\n)\n'
    assert i18n._parse_apple_languages(out) == ["de-DE", "en-US", "fr"]


def test_windows_langid_maps_to_tag():
    assert i18n._langid_to_tag(0x0407) == "de"     # Deutsch (Deutschland)
    assert i18n._langid_to_tag(0x0C07) == "de"     # Deutsch (Österreich)
    assert i18n._langid_to_tag(0x0409) == "en"
    assert i18n._langid_to_tag(0x040C) == ""       # Französisch: nicht unterstützt


def test_windows_locale_name_german_is_recognised(monkeypatch):
    """locale.getlocale() liefert unter Windows 'German_Germany', nicht 'de_DE'."""
    _os_langs(monkeypatch, [])
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("German_Germany", "1252"))
    assert i18n.current() == "de"


# ------------------------------------------------------- Vollständigkeit
def test_every_entry_has_english_and_german():
    for key, value in i18n.STRINGS.items():
        assert isinstance(value, tuple) and len(value) == 2, key
        en, de = value
        assert en.strip() and de.strip(), key


def test_placeholders_match_between_languages():
    import string
    fmt = string.Formatter()
    for key, (en, de) in i18n.STRINGS.items():
        names_en = {n for _, n, _, _ in fmt.parse(en) if n}
        names_de = {n for _, n, _, _ in fmt.parse(de) if n}
        assert names_en == names_de, key


# --------------------------------------------- H115 Tastennamen auf macOS
@pytest.mark.parametrize("key,en,de", [
    ("chord_ctrl_meta", "Control + Command (⌃⌘)", "Control + Befehl (⌃⌘)"),
    ("chord_alt_meta", "Option + Command (⌥⌘)", "Wahl + Befehl (⌥⌘)"),
    ("chord_ctrl_alt", "Control + Option (⌃⌥)", "Control + Wahl (⌃⌥)"),
])
def test_mac_uses_mac_key_names(monkeypatch, key, en, de):
    monkeypatch.setattr(i18n, "IS_MAC", True)
    i18n.set_language("en")
    assert i18n.tr(key) == en
    i18n.set_language("de")
    assert i18n.tr(key) == de


def test_mac_never_mentions_windows_key(monkeypatch):
    monkeypatch.setattr(i18n, "IS_MAC", True)
    for lang in ("en", "de"):
        i18n.set_language(lang)
        for key in ("chord_ctrl_meta", "chord_alt_meta", "chord_ctrl_alt"):
            text = i18n.tr(key)
            assert "Windows" not in text and "Meta" not in text, text


def test_other_platforms_keep_their_names(monkeypatch):
    monkeypatch.setattr(i18n, "IS_MAC", False)
    i18n.set_language("en")
    assert i18n.tr("chord_ctrl_meta") == "Ctrl + Meta (Windows key)"


def test_every_chord_label_has_a_mac_variant():
    from quassel import config
    for key in config.CHORD_LABEL_KEYS.values():
        assert key + "_mac" in i18n.STRINGS, key


# ------------------------------------------- D57 / Tray-Not-Aus (bau-windows OPEN 3, 5)
@pytest.mark.parametrize("lang,text", [
    ("en", "Text is on the clipboard – press Ctrl+V"),
    ("de", "Text liegt in der Zwischenablage – Strg+V drücken"),
])
def test_clipboard_only_hint_comes_from_the_table(monkeypatch, lang, text):
    from quassel import dictation
    monkeypatch.setattr(i18n, "IS_MAC", False)
    i18n.set_language(lang)
    assert "paste_clipboard_only" in i18n.STRINGS
    assert dictation.clipboard_only_text() == text == i18n.tr("paste_clipboard_only")


@pytest.mark.parametrize("lang,text", [("en", "Stop dictation now"),
                                       ("de", "Diktat sofort beenden")])
def test_tray_panic_label_is_shared(lang, text):
    """Windows-Tray (bisher fest in win/app.py:_panic_label) und macOS-Menü
    benutzen denselben Schlüssel."""
    i18n.set_language(lang)
    assert i18n.tr("tray_panic") == text


def test_wake_listening_feedback_has_both_languages():
    i18n.set_language("de")
    assert i18n.tr("wake_listening") == "Hört zu…"
    i18n.set_language("en")
    assert i18n.tr("wake_listening") == "Listening…"
