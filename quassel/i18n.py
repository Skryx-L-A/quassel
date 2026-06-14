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
    "preparing": ("Preparing {item}…", "Bereite {item} vor…"),
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
    "no_mic": ("No microphone — is it on and connected?",
               "Kein Mikrofon — ist es an und verbunden?"),
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
    # --- Kommandos / Daemon ---
    "pressed_enter": ("Pressed Enter", "Eingabetaste gedrückt"),
    # --- Streaming-Modus "word" ---
    "streaming_word": ("Word by word (corrects as needed)",
                       "Wort für Wort (korrigiert bei Bedarf)"),
    # --- Wake-Word ---
    "nav_wakeword": ("Wake word", "Wake-Word"),
    "sec_wakeword": ("Wake word (hands-free)", "Wake-Word (Freisprechen)"),
    "wakeword_enable": ("Listen for a wake word to start dictation",
                        "Auf ein Wake-Word hören, um das Diktat zu starten"),
    "wakeword_hint": ("When on, Quassel listens locally for your phrase; say it, then speak — it stops on its own after a short silence. Everything stays on this computer; nothing is ever sent. Uses a little more CPU while armed.",
                      "Wenn aktiv, hört Quassel lokal auf deine Phrase; sag sie, dann sprich — nach kurzer Stille stoppt es von selbst. Alles bleibt auf diesem PC, nichts wird gesendet. Verbraucht im Bereitschaftsmodus etwas mehr CPU."),
    "wakeword_phrase": ("Wake phrase", "Wake-Phrase"),
    "wakeword_change_hint": ("Change this to anything easy for you to say — “Hey Quassel” is just the default and may be awkward in some languages.",
                             "Ändere das zu etwas, das dir leicht über die Lippen geht — „Hey Quassel“ ist nur die Vorgabe und kann in manchen Sprachen ungewohnt sein."),
    # --- Programmier-Diktat ---
    "programmer_enable": ("Programmer mode (speak symbols & camelCase)",
                          "Programmier-Modus (Symbole & camelCase sprechen)"),
    "programmer_hint": ("Speak symbols like “open paren”, “semicolon”, “arrow”, and identifiers like “camel case user name end case” → userName.",
                        "Sprich Symbole wie „Klammer auf“, „Semikolon“, „Pfeil“ und Bezeichner wie „camel case user name end case“ → userName."),
    # --- Auto-Lernen ---
    "auto_learn": ("Auto-learn names & jargon into the dictionary",
                   "Namen & Fachwörter automatisch ins Wörterbuch lernen"),
    "auto_learn_hint": ("Words that look like names or jargon (CamelCase, ALLCAPS, with digits) are added automatically to improve future recognition.",
                        "Wörter, die wie Namen/Fachbegriffe aussehen (CamelCase, GROSSBUCHSTABEN, mit Ziffern), werden automatisch ergänzt — für bessere Erkennung."),
    # --- Gemischte Sprache ---
    "lang_mixed": ("Mixed (German + English)", "Gemischt (Deutsch + Englisch)"),
    # --- Textersetzungen ---
    "nav_replace": ("Replacements", "Ersetzungen"),
    "sec_replace": ("Text replacements / snippets", "Textersetzungen / Snippets"),
    "replace_enable": ("Apply text replacements", "Textersetzungen anwenden"),
    "replace_hint": ("One rule per line as trigger=expansion, e.g. omw=on my way. Lines starting with # are ignored.",
                     "Eine Regel pro Zeile als trigger=ersatz, z. B. lg=Liebe Grüße. Zeilen mit # werden ignoriert."),
    # --- Statistik ---
    "sec_stats": ("Usage statistics (local only)", "Nutzungsstatistik (nur lokal)"),
    "stats_enable": ("Count words dictated & time saved", "Diktierte Wörter & gesparte Zeit zählen"),
    "stats_none": ("No dictations yet.", "Noch keine Diktate."),
    "stats_reset": ("Reset statistics", "Statistik zurücksetzen"),
    "stats_line": ("{words} words in {sessions} sessions — about {minutes} min of typing saved",
                   "{words} Wörter in {sessions} Sitzungen — etwa {minutes} min Tippen gespart"),
    # --- Zurücksetzen (#31) ---
    "sec_reset": ("Reset", "Zurücksetzen"),
    "reset_defaults": ("Restore all settings to defaults", "Alle Einstellungen zurücksetzen"),
    "reset_hint": ("Puts every setting back to its original value. Your dictionary, replacements, history and downloaded model are kept.",
                   "Setzt jede Einstellung auf den Ausgangswert. Wörterbuch, Ersetzungen, Verlauf und das heruntergeladene Modell bleiben erhalten."),
    "reset_done": ("Settings restored to defaults.", "Einstellungen zurückgesetzt."),
    # --- Update-Prüfung ---
    "update_label": ("Updates", "Aktualisierungen"),
    "update_check_startup": ("Check for new versions on start", "Beim Start auf neue Versionen prüfen"),
    "update_check_now": ("Check now", "Jetzt prüfen"),
    "update_checking": ("Checking…", "Prüfe…"),
    "update_available": ("Update available: {latest} (you have {current})",
                         "Update verfügbar: {latest} (installiert: {current})"),
    "update_none": ("You're up to date ({current}).", "Du bist aktuell ({current})."),
    "update_failed": ("Couldn't check right now.", "Konnte gerade nicht prüfen."),
    "update_get": ("Get it", "Holen"),
    # --- Datei transkribieren (#21) ---
    "nav_file": ("Transcribe file", "Datei transkribieren"),
    "sec_file": ("Transcribe an audio or video file", "Audio- oder Videodatei transkribieren"),
    "file_hint": ("Pick a file — Quassel transcribes it locally and copies the text to your clipboard.",
                  "Datei wählen — Quassel transkribiert sie lokal und kopiert den Text in die Zwischenablage."),
    "file_pick": ("Choose file…", "Datei wählen…"),
    "file_running": ("Transcribing {name}…", "Transkribiere {name}…"),
    "file_done": ("Done — copied to clipboard. {chars} characters.",
                  "Fertig — in die Zwischenablage kopiert. {chars} Zeichen."),
    "file_failed": ("Couldn't transcribe that file.", "Konnte die Datei nicht transkribieren."),
    "file_no_ffmpeg": ("ffmpeg is required to read this file type.",
                       "Für diesen Dateityp wird ffmpeg benötigt."),
    "copy": ("Copy", "Kopieren"),
    # --- Onboarding (#27) ---
    "ob_title": ("Welcome to Quassel", "Willkommen bei Quassel"),
    "ob_body": ("Quassel turns your voice into text wherever your cursor is — fully offline.\n\nHold {chord}, speak, release. That's it.\n\nThere's nothing you must configure: you can just close this and start dictating. Everything below is optional.",
                "Quassel macht aus deiner Stimme Text — überall dort, wo dein Cursor ist, komplett offline.\n\n{chord} halten, sprechen, loslassen. Das war's.\n\nDu musst nichts einstellen: Du kannst das einfach schließen und loslegen. Alles Weitere ist optional."),
    "ob_wake": ("Optional hands-free: turn on a wake word in settings and say a phrase to start. The default is “Hey Quassel” — change it to anything you like, handy if that's awkward to say in your language.",
                "Optional freisprechen: aktiviere in den Einstellungen ein Wake-Word und starte per Phrase. Vorgabe ist „Hey Quassel“ — ändere es beliebig, praktisch, wenn das in deiner Sprache schwer auszusprechen ist."),
    "ob_open_settings": ("Open settings", "Einstellungen öffnen"),
    "ob_close": ("Got it — just start dictating", "Verstanden — einfach loslegen"),
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
