"""Tiny bilingual string table (English base, German translation).

UI language: config [ui] language = auto|en|de; auto follows the system locale.
"""
import locale
import os

STRINGS = {
    "app_name": ("Quassel", "Quassel"),
    "on": ("Quassel is ON", "Quassel ist AN"),
    "off": ("Quassel is OFF", "Quassel ist AUS"),
    "turn_on": ("Turn on", "Einschalten"),
    "turn_off": ("Turn off", "Ausschalten"),
    "hint": ("Hold {chord} = speak, release to insert\nDouble-tap {chord} = hands-free, press once to insert",
             "{chord} halten = sprechen, loslassen fügt ein\n{chord} 2× tippen = freihändig, 1× drücken fügt ein"),
    "sec_pill": ("Pill overlay", "Pille (Overlay)"),
    "pill_show": ("Show pill", "Pille anzeigen"),
    "pill_size": ("Size", "Größe"),
    "pill_opacity": ("Opacity", "Transparenz"),
    "pill_preview": ("Show live preview text in the pill", "Live-Vorschautext in der Pille zeigen"),
    "sec_hotkey": ("Hotkey", "Hotkey"),
    "chord_ctrl_meta": ("Ctrl + Meta (Windows key)", "Strg + Meta (Windows-Taste)"),
    "chord_alt_meta": ("Alt + Meta", "Alt + Meta"),
    "chord_ctrl_alt": ("Ctrl + Alt", "Strg + Alt"),
    "sec_dictating": ("While dictating", "Beim Diktieren"),
    "mute_label": ("Audio", "Audio"),
    "mute_off": ("Do nothing", "Nichts ändern"),
    "mute_music": ("Pause music / media", "Musik / Medien pausieren"),
    "mute_all": ("Mute all system sound", "Gesamtton stummschalten"),
    "mute_hint": ("Quiets playback while you dictate and restores it afterwards — so audio doesn't bleed into the mic or distract you.",
                  "Macht die Wiedergabe beim Diktieren leise und stellt sie danach wieder her — damit kein Ton ins Mikrofon gerät oder ablenkt."),
    "sec_streaming": ("Live typing (streaming)", "Live-Tippen (Streaming)"),
    "streaming_enable": ("Type into the target window while I speak",
                         "Während des Sprechens ins Zielfenster tippen"),
    "streaming_hint": ("Only in hands-free mode (double-tap). While the hotkey is held, the system can't receive typed text safely, so held dictation always inserts at the end. Line breaks are inserted only when you finish.",
                       "Nur im Freihand-Modus (Doppeltipp). Solange der Hotkey gehalten wird, kann das System getippten Text nicht sicher empfangen — gehaltenes Diktat fügt daher immer am Ende ein. Zeilenumbrüche kommen erst beim Beenden."),
    "streaming_mode": ("Mode", "Modus"),
    "streaming_stable": ("Stable words only (no corrections)", "Nur stabile Wörter (keine Korrekturen)"),
    "streaming_aggressive": ("Type immediately, correct as needed", "Sofort tippen, bei Bedarf korrigieren"),
    "sec_speech": ("Speech recognition", "Spracherkennung"),
    "language": ("Spoken language", "Gesprochene Sprache"),
    "lang_auto": ("Detect automatically", "Automatisch erkennen"),
    "lang_de": ("German", "Deutsch"),
    "lang_en": ("English", "Englisch"),
    "punctuation": ("Spoken punctuation (“comma”, “new line”)", "Gesprochene Satzzeichen („Komma“, „neue Zeile“)"),
    "commands": ("Voice commands (“scratch that”)", "Sprachkommandos („lösch das“)"),
    "model": ("Whisper model", "Whisper-Modell"),
    "downloading": ("Downloading {model}…", "Lade {model} herunter…"),
    "model_switched": ("Model switched: {model}", "Modell gewechselt: {model}"),
    "download_failed": ("Download failed: {err}", "Download fehlgeschlagen: {err}"),
    "sec_mic": ("Microphone", "Mikrofon"),
    "mic_default": ("System default (follows sound settings)", "System-Standard (folgt den Soundeinstellungen)"),
    "mic_test": ("Test microphone (records 2.5 s)", "Mikrofon testen (2,5 s Aufnahme)"),
    "mic_testing": ("Recording… speak now!", "Aufnahme läuft… jetzt sprechen!"),
    "mic_transcribing": ("Transcribing…", "Transkribiere…"),
    "mic_nothing": ("No audio captured — check your microphone!", "Keine Audiodaten — Mikrofon prüfen!"),
    "mic_result": ("Recognized: {text}", "Erkannt: {text}"),
    "no_server": ("Whisper server unreachable", "Whisper-Server nicht erreichbar"),
    "sec_dict": ("Personal dictionary", "Persönliches Wörterbuch"),
    "dict_hint": ("One word or name per line — improves recognition of names and jargon.",
                  "Ein Wort/Name pro Zeile — verbessert die Erkennung von Namen und Fachwörtern."),
    "sec_history": ("History", "Verlauf"),
    "history_enable": ("Keep history (stored only on this computer)", "Verlauf speichern (nur lokal auf diesem PC)"),
    "history_copy": ("Click an entry to copy it again", "Eintrag anklicken, um ihn erneut zu kopieren"),
    "history_clear": ("Clear history", "Verlauf löschen"),
    "copied": ("Copied!", "Kopiert!"),
    "sec_system": ("System", "System"),
    "autostart": ("Start Quassel when I log in", "Quassel beim Anmelden starten"),
    "ui_language": ("App language", "App-Sprache"),
    "ui_auto": ("Automatic (system)", "Automatisch (System)"),
    "close_note": ("Closing this window does not stop Quassel.",
                   "Dieses Fenster zu schließen beendet Quassel nicht."),
    "recording": ("Recording", "Aufnahme"),
    "transcribing": ("Transcribing…", "Transkribiere…"),
    "too_short": ("Recording too short", "Aufnahme zu kurz"),
    "nothing": ("Didn't catch that", "Nichts verstanden"),
    "deleted": ("Last dictation deleted", "Letztes Diktat gelöscht"),
    "canceled_tap": ("Canceled (single tap — double-tap or hold)",
                     "Abgebrochen (nur 1× getippt — doppelt tippen oder halten)"),
    "canceled_key": ("Canceled (other key pressed)", "Abgebrochen (andere Taste gedrückt)"),
    "ready": ("Quassel ready", "Quassel bereit"),
    "settings": ("Settings", "Einstellungen"),
    "quit": ("Quit Quassel", "Quassel beenden"),
    "nav_general": ("General", "Allgemein"),
    "nav_speech": ("Speech", "Spracherkennung"),
    "nav_dict": ("Dictionary", "Wörterbuch"),
    "nav_history": ("History", "Verlauf"),
    "nav_system": ("System", "System"),
    "sec_status": ("Status", "Status"),
    "about": ("About", "Über"),
    "about_text": ("Quassel {version} — local, private voice typing.\nOpen source (MIT).",
                   "Quassel {version} — lokale, private Spracheingabe.\nOpen Source (MIT)."),
    "repo": ("Project page", "Projektseite"),
}

_lang = None


def set_language(lang):
    global _lang
    _lang = lang if lang in ("en", "de") else None


def current():
    if _lang:
        return _lang
    loc = os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
    if not loc:
        try:
            loc = locale.getlocale()[0] or ""
        except ValueError:
            loc = ""
    return "de" if loc.lower().startswith("de") else "en"


def tr(key, **kw):
    en, de = STRINGS.get(key, (key, key))
    s = de if current() == "de" else en
    return s.format(**kw) if kw else s
