"""Tiny bilingual string table (English base, German translation).

UI language: config [ui] language = auto|en|de; auto follows the system:
LC_ALL / LC_MESSAGES as explicit overrides, then the language chosen in the
operating system (macOS AppleLanguages, Windows UI language), then LANG and
the C locale.
"""
import functools
import locale
import os
import subprocess
import sys

SUPPORTED = ("en", "de")
# Auf macOS gewinnt ein Eintrag "<key>_mac" über "<key>" (Tastennamen).
IS_MAC = sys.platform == "darwin"

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
    "pill_movable": ("Pill can be moved", "Pille frei verschiebbar"),
    "pill_click_center": ("Click on the pill opens the control center",
                           "Klick auf die Pille öffnet das Kontrollzentrum"),
    "pill_movable_linux": ("On Linux only the portable pill can be moved, and only under X11. The standard pill always sits at the bottom center of the active screen.",
                           "Unter Linux lässt sich nur die Pille der portablen Fassung verschieben, und nur unter X11. Die Standard-Pille sitzt immer unten mittig auf dem aktiven Bildschirm."),
    "pill_reset_pos": ("Reset position", "Position zurücksetzen"),
    "pill_preview": ("Show live preview text in the pill", "Live-Vorschautext in der Pille zeigen"),
    "sec_hotkey": ("Hotkey", "Hotkey"),
    "chord_ctrl_meta": ("Ctrl + Meta (Windows key)", "Strg + Meta (Windows-Taste)"),
    "chord_alt_meta": ("Alt + Meta", "Alt + Meta"),
    "chord_ctrl_alt": ("Ctrl + Alt", "Strg + Alt"),
    # macOS: dieselben Chords mit den Namen der Mac-Tastatur (tr() wählt die
    # _mac-Variante dort automatisch). Meta ist auf dem Mac die Befehlstaste.
    "chord_ctrl_meta_mac": ("Control + Command (⌃⌘)", "Control + Befehl (⌃⌘)"),
    "chord_alt_meta_mac": ("Option + Command (⌥⌘)", "Wahl + Befehl (⌥⌘)"),
    "chord_ctrl_alt_mac": ("Control + Option (⌃⌥)", "Control + Wahl (⌃⌥)"),
    "sec_dictating": ("While dictating", "Beim Diktieren"),
    "mute_label": ("Audio", "Audio"),
    "mute_off": ("Do nothing", "Nichts ändern"),
    "mute_music": ("Pause music / media", "Musik / Medien pausieren"),
    "mute_all": ("Mute all system sound", "Gesamtton stummschalten"),
    "mute_hint": ("Quiets playback while you dictate and restores it afterwards — so audio doesn't bleed into the mic or distract you.",
                  "Macht die Wiedergabe beim Diktieren leise und stellt sie danach wieder her — damit kein Ton ins Mikrofon gerät oder ablenkt."),
    "beep_enable": ("Beep when recording starts and stops",
                    "Ton bei Aufnahme-Start und -Ende"),
    "beep_hint": ("A short rising tone when it starts listening and a falling tone when it stops — handy with headphones.",
                  "Ein kurzer aufsteigender Ton beim Start des Zuhörens, ein absteigender beim Stopp — praktisch mit Kopfhörern."),
    "beep_out_label": ("Play tones on", "Töne abspielen über"),
    "beep_out_system": ("System default output (follows macOS)", "System-Standard (folgt macOS)"),
    "beep_out_hint": ("The tones normally play wherever macOS is currently sending sound — so they follow you to headphones or a new output automatically. Pick a fixed device here only if you want the tones somewhere else.",
                      "Die Töne spielen normalerweise dort, wohin macOS gerade jeden Ton schickt — sie folgen also automatisch zu Kopfhörern oder einem neuen Ausgabegerät. Ein festes Gerät wählst Du hier nur, wenn Du die Töne bewusst woanders hören willst."),
    "sec_streaming": ("Live typing (streaming)", "Live-Tippen (Streaming)"),
    "streaming_enable": ("Type into the target window while I speak",
                         "Während des Sprechens ins Zielfenster tippen"),
    "streaming_hint": ("Only in hands-free mode (double-tap). While the hotkey is held, the system can't receive typed text safely, so held dictation always inserts at the end. Line breaks are inserted only when you finish.",
                       "Nur im Freihand-Modus (Doppeltipp). Solange der Hotkey gehalten wird, kann das System getippten Text nicht sicher empfangen — gehaltenes Diktat fügt daher immer am Ende ein. Zeilenumbrüche kommen erst beim Beenden."),
    "streaming_mode": ("Mode", "Modus"),
    "streaming_stable": ("Stable words only (no corrections)", "Nur stabile Wörter (keine Korrekturen)"),
    "streaming_aggressive": ("Confirmed words, shortest delay", "Bestätigte Wörter, kürzeste Verzögerung"),
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
    "mic_refresh": ("Refresh microphone list", "Mikrofonliste aktualisieren"),
    "mic_default": ("System default (follows sound settings)", "System-Standard (folgt den Soundeinstellungen)"),
    "mic_test": ("Test microphone (records 2.5 s)", "Mikrofon testen (2,5 s Aufnahme)"),
    "mic_testing": ("Recording… speak now!", "Aufnahme läuft… jetzt sprechen!"),
    "mic_transcribing": ("Transcribing…", "Transkribiere…"),
    "mic_nothing": ("No audio captured — check your microphone!", "Keine Audiodaten — Mikrofon prüfen!"),
    "mic_result": ("Recognized: {text}", "Erkannt: {text}"),
    "mic_silent": ("No sound from the microphone. Check mute, the selected input and microphone access.",
                   "Kein Ton vom Mikrofon. Stummschaltung, gewähltes Gerät und Mikrofonfreigabe prüfen."),
    "no_server": ("Whisper server unreachable", "Whisper-Server nicht erreichbar"),
    # --- Konkrete Fehlermeldungen (H113): was ist los, was hilft ---
    "err_server_down": ("The speech server isn't answering. If Quassel just started, it may still be loading the model. Try again in a moment.",
                        "Der Sprach-Server antwortet nicht. Kurz nach dem Start lädt er eventuell noch das Modell. Gleich noch einmal versuchen."),
    "err_model_missing": ("The speech model is missing ({model}). Choose a model under Speech recognition to download it.",
                          "Das Sprachmodell fehlt ({model}). Unter Spracherkennung ein Modell wählen, dann wird es geladen."),
    "err_timeout": ("The speech server took too long to answer. A smaller model is faster.",
                    "Der Sprach-Server hat zu lange gebraucht. Ein kleineres Modell ist schneller."),
    "err_transcribe": ("The speech server couldn't transcribe this recording.",
                       "Der Sprach-Server konnte diese Aufnahme nicht umwandeln."),
    "err_mic_denied": ("Quassel isn't allowed to use the microphone. Allow it in System Settings → Privacy & Security → Microphone.",
                       "Quassel darf das Mikrofon nicht benutzen. Freigabe in Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon."),
    "err_mic_open": ("The microphone couldn't be opened. Another app may be using it, or it was unplugged.",
                     "Das Mikrofon ließ sich nicht öffnen. Vielleicht nutzt es eine andere App, oder es wurde abgezogen."),
    "err_recorder_missing": ("The recording program is missing: {tool}.",
                             "Das Aufnahmeprogramm fehlt: {tool}."),
    # Einfügen gescheitert, Text liegt aber in der Zwischenablage (D57)
    "daemon_crashed": ("Dictation service stopped (exit {code}). Turn it on to retry.",
                       "Diktierdienst beendet (Code {code}). Zum erneuten Versuch einschalten."),
    "paste_clipboard_only": ("Text is on the clipboard – press Ctrl+V",
                             "Text liegt in der Zwischenablage – Strg+V drücken"),
    "err_paste_blocked": ("The text couldn't be inserted. On macOS, allow Quassel in System Settings → Privacy & Security → Accessibility. The text is on the clipboard.",
                          "Der Text ließ sich nicht einfügen. Unter macOS Quassel in Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen freigeben. Der Text liegt in der Zwischenablage."),
    # macOS-Rechte (hotkey_mac.py), mit dem tatsächlich eingestellten Chord
    "perm_input_monitoring": ("Quassel needs the “Input Monitoring” permission to notice the dictation hotkey ({chord}). Allow it in System Settings → Privacy & Security → Input Monitoring, then restart Quassel.",
                              "Quassel braucht die Berechtigung „Eingabeüberwachung“, um den Diktier-Hotkey ({chord}) zu erkennen. Bitte in Systemeinstellungen → Datenschutz & Sicherheit → Eingabeüberwachung freigeben und Quassel danach neu starten."),
    "perm_accessibility": ("Quassel needs the “Accessibility” permission to insert dictated text. Allow it in System Settings → Privacy & Security → Accessibility, then restart Quassel.",
                           "Quassel braucht die Berechtigung „Bedienungshilfen“, um diktierten Text einzufügen. Bitte in Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen freigeben und Quassel danach neu starten."),
    "no_mic": ("No microphone — is it on and connected?",
               "Kein Mikrofon — ist es an und verbunden?"),
    "sec_dict": ("Personal dictionary", "Persönliches Wörterbuch"),
    "dict_hint": ("One word or name per line — improves recognition of names and jargon.",
                  "Ein Wort/Name pro Zeile — verbessert die Erkennung von Namen und Fachwörtern."),
    "dict_effective": ("Words currently used ({count}): {words}",
                       "Wörter, die derzeit wirken ({count}): {words}"),
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
    "panic_stopped": ("Dictation stopped", "Diktat sofort beendet"),
    "audio_restart": ("Audio device got stuck — restarting the dictation service",
                      "Audiogerät klemmte — Quassel startet den Diktat-Dienst neu"),
    "rescued": ("recording saved: {path}", "Aufnahme gesichert: {path}"),
    "nothing_running": ("No dictation is running", "Es läuft kein Diktat"),
    "ready": ("Quassel ready", "Quassel bereit"),
    # --- macOS-Menüleiste ---
    "tray_ready": ("Ready", "Bereit"),
    "tray_recording": ("Recording", "Aufnahme"),
    "tray_transcribing": ("Transcribing", "Transkription"),
    "tray_done": ("Done", "Fertig"),
    "tray_error": ("Error", "Fehler"),
    "tray_off": ("Off", "Aus"),
    # Not-Aus im Tray-Menü (macOS traymac.py, Windows win/app.py)
    "tray_panic": ("Stop dictation now", "Diktat sofort beenden"),
    "tray_open_center": ("Open Control Center", "Kontrollzentrum öffnen"),
    "tray_secure": ("Secure input is on, hotkey paused", "Sichere Eingabe aktiv, Hotkey pausiert"),
    "secure_input": ("Secure input is on (a password field, or Secure Keyboard Entry in Terminal). Quassel can't hear the hotkey until it ends.",
                     "Sichere Eingabe ist aktiv (Passwortfeld oder „Sichere Tastatureingabe“ im Terminal). Quassel hört den Hotkey erst danach wieder."),
    # --- macOS-App (Hinweise der Menüleisten-App) ---
    "daemon_giveup": ("The dictation service keeps restarting. Please check the log: {path}",
                      "Der Diktat-Dienst startet wiederholt neu, bitte Log prüfen: {path}"),
    "ffmpeg_missing": ("ffmpeg is missing, so nothing can be recorded.\nInstall it with: brew install ffmpeg",
                       "ffmpeg fehlt, ohne ffmpeg keine Aufnahme.\nInstallieren: brew install ffmpeg"),
    "settings": ("Settings", "Einstellungen"),
    "quit": ("Quit Quassel", "Quassel beenden"),
    "nav_sections": ("Sections", "Bereiche"),
    "nav_general": ("General", "Allgemein"),
    "nav_pill": ("Pill", "Pille"),
    "nav_dictating": ("While dictating", "Beim Diktieren"),
    "nav_speech": ("Speech", "Spracherkennung"),
    "nav_mic": ("Microphone", "Mikrofon"),
    "nav_beta": ("Beta features", "Beta-Funktionen"),
    # --- Auto-Stopp / Spracherkennung der Stille (vad.py) ---
    "sec_vad": ("Auto-stop after silence (Beta)", "Auto-Stopp nach Stille (Beta)"),
    "vad_autostop": ("Stop hands-free dictation by itself after a pause",
                     "Freihand-Diktat nach einer Pause selbst beenden"),
    "vad_autostop_hint": ("Only in hands-free mode (double-tap). Holding the hotkey is not affected.",
                          "Nur im Freihand-Modus (Doppeltipp). Gehaltenes Diktat bleibt unverändert."),
    "vad_silence": ("Pause before stopping", "Pause bis zum Stopp"),
    "vad_backend": ("Speech detection", "Spracherkennung der Pausen"),
    "vad_backend_auto": ("Automatic", "Automatisch"),
    "vad_backend_rms": ("Loudness only", "Nur Lautstärke"),
    "vad_silero_model": ("Silero model (.onnx)", "Silero-Modell (.onnx)"),
    "choose_file": ("Choose…", "Wählen…"),
    "vad_silero_hint": ("Automatic uses Silero when a model file is set and onnxruntime is installed, otherwise loudness. Silero tells speech from noise more reliably.",
                        "Automatisch nutzt Silero, wenn eine Modelldatei eingetragen und onnxruntime installiert ist, sonst die Lautstärke. Silero unterscheidet Sprache und Geräusch zuverlässiger."),
    # --- Schlüsselwort-Erkenner für das Wake-Word (wakeword.py) ---
    "wake_spotter_model": ("Keyword model (.onnx)", "Schlüsselwort-Modell (.onnx)"),
    "wake_spotter_hint": ("Optional. A keyword spotter (openWakeWord format) listens for the phrase before speech recognition runs, which saves power and false starts. Quassel ships no model; train your own phrase and pick the file here.",
                          "Optional. Ein Schlüsselwort-Erkenner (openWakeWord-Format) hört vor der Spracherkennung auf die Phrase, das spart Strom und Fehlstarts. Quassel liefert kein Modell mit; die eigene Phrase selbst trainieren und die Datei hier wählen."),
    "wake_threshold": ("Sensitivity threshold", "Auslöseschwelle"),
    "wake_threshold_hint": ("Higher means fewer false starts but more missed phrases.",
                            "Höher heißt weniger Fehlstarts, aber öfter überhört."),
    "wake_verify": ("Confirm the phrase with speech recognition before starting",
                    "Phrase vor dem Start zusätzlich mit der Spracherkennung prüfen"),
    # --- Vorlaufpuffer ([speech] preroll_ms) ---
    "sec_preroll": ("Pre-roll (Beta)", "Vorlaufpuffer (Beta)"),
    "preroll_ms": ("Keep before the next dictation", "Vor dem nächsten Diktat behalten"),
    "preroll_off": ("Off", "Aus"),
    "preroll_hint": ("After a dictation the microphone stays open for three seconds, so a dictation started right after it keeps this much audio from before the hotkey. Linux and macOS; applies after turning Quassel off and on.",
                     "Nach einem Diktat bleibt das Mikrofon drei Sekunden offen, damit ein gleich danach begonnenes Diktat so viel Ton von vor dem Hotkey behält. Linux und macOS; wirkt nach Aus- und Einschalten von Quassel."),
    "preroll_mac_hint": ("On macOS the orange microphone indicator stays visible while the microphone is open.",
                         "Unter macOS bleibt die orange Mikrofonanzeige sichtbar, solange das Mikrofon offen ist."),
    # --- Per-App-Profile (profiles.py) ---
    "sec_profiles": ("Per-app profiles", "Profile je Anwendung"),
    "profiles_hint": ("Different settings per application, e.g. no Enter and programmer mode in the terminal. They live in their own file next to the settings; without that file nothing changes.",
                      "Andere Einstellungen je Anwendung, z. B. im Terminal kein Enter und Programmier-Modus. Sie stehen in einer eigenen Datei neben den Einstellungen; ohne diese Datei ändert sich nichts."),
    "profiles_open": ("Create or open the profiles file", "Profil-Datei anlegen oder öffnen"),
    "profiles_created": ("Created with the default profiles: {path}", "Mit den Vorgabe-Profilen angelegt: {path}"),
    "profiles_opened": ("Opened: {path}", "Geöffnet: {path}"),
    "profiles_failed": ("Couldn't create the profiles file.", "Die Profil-Datei ließ sich nicht anlegen."),
    "beta_intro": ("Features that are not finished yet. They are off by default and may change or behave unexpectedly.",
                   "Funktionen, die noch nicht fertig sind. Sie sind standardmäßig aus und können sich ändern oder unerwartet verhalten."),
    "nav_dict": ("Dictionary", "Wörterbuch"),
    "nav_history": ("History", "Verlauf"),
    "nav_system": ("System", "System"),
    "sec_status": ("Status", "Status"),
    "about": ("About", "Über"),
    "about_text": ("Quassel {version} — local, private voice typing.\nSource available (PolyForm Noncommercial 1.0.0).",
                   "Quassel {version} — lokale, private Spracheingabe.\nQuelloffen (PolyForm Noncommercial 1.0.0)."),
    "repo": ("Project page", "Projektseite"),
    # --- Kommandos / Daemon ---
    "pressed_enter": ("Pressed Enter", "Eingabetaste gedrückt"),
    "enter_blocked": ("Enter is blocked in this app", "Enter in dieser App gesperrt"),
    # --- Streaming-Modus "word" ---
    "streaming_word": ("Word by word (confirmed words only)",
                       "Wort für Wort (nur bestätigte Wörter)"),
    # --- Wake-Word ---
    "nav_wakeword": ("Wake word", "Wake-Word"),
    "sec_wakeword": ("Wake word - hands-free (Beta)", "Wake-Word - Freisprechen (Beta)"),
    "wakeword_beta": ("Beta - not reliable yet: it may miss the phrase or trigger by mistake. "
                      "Off by default; work in progress (tracked on GitHub).",
                      "Beta - noch nicht zuverlässig: erkennt die Phrase nicht immer oder löst "
                      "versehentlich aus. Standardmäßig aus; wird noch verbessert (auf GitHub verfolgt)."),
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
    "file_hint_manual": ("Pick a file — Quassel transcribes it locally. Nothing is copied until you press Copy.",
                         "Datei wählen — Quassel transkribiert sie lokal. Kopiert wird erst, wenn Du auf Kopieren drückst."),
    "file_format": ("Format", "Format"),
    "file_format_text": ("Plain text", "Reiner Text"),
    "file_format_srt": ("Subtitles (SRT)", "Untertitel (SRT)"),
    "file_format_vtt": ("Subtitles (WebVTT)", "Untertitel (WebVTT)"),
    "file_cancel": ("Cancel", "Abbrechen"),
    "file_canceled": ("Canceled.", "Abgebrochen."),
    "file_progress": ("Progress", "Fortschritt"),
    "file_ready": ("Done — {chars} characters. Press Copy to put them on the clipboard.",
                   "Fertig — {chars} Zeichen. Mit Kopieren in die Zwischenablage legen."),
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
    # --- Onboarding in Schritten (G110) ---
    "ob_back": ("Back", "Zurück"),
    "ob_next": ("Next", "Weiter"),
    "ob_finish": ("Finish", "Fertig"),
    "ob_step": ("Step {n} of {total}", "Schritt {n} von {total}"),
    "ob_perm_title": ("Permissions", "Berechtigungen"),
    "ob_perm_hint": ("Quassel needs these permissions to hear the hotkey, record and insert text. Nothing leaves this computer.",
                     "Quassel braucht diese Rechte, um den Hotkey zu hören, aufzunehmen und Text einzufügen. Nichts verlässt diesen Rechner."),
    "ob_perm_mic": ("Microphone", "Mikrofon"),
    "ob_perm_input": ("Input Monitoring (hears the hotkey)", "Eingabeüberwachung (hört den Hotkey)"),
    "ob_perm_access": ("Accessibility (inserts the text)", "Bedienungshilfen (fügt den Text ein)"),
    "ob_perm_input_linux": ("Keyboard access (member of the “input” group)", "Tastaturzugriff (Mitglied der Gruppe „input“)"),
    "ob_perm_ok": ("Allowed", "Erlaubt"),
    "ob_perm_missing": ("Not allowed yet", "Noch nicht erlaubt"),
    "ob_perm_unknown": ("Asked on first use", "Wird beim ersten Mal gefragt"),
    "ob_perm_open": ("Open System Settings", "Systemeinstellungen öffnen"),
    "ob_perm_none": ("Windows needs no extra permissions for Quassel.", "Windows braucht für Quassel keine zusätzlichen Rechte."),
    "ob_perm_restart": ("After allowing Input Monitoring or Accessibility, quit and reopen Quassel once.",
                        "Nach dem Erlauben von Eingabeüberwachung oder Bedienungshilfen Quassel einmal beenden und neu öffnen."),
    "ob_mic_title": ("Microphone test", "Mikrofontest"),
    "ob_mic_hint": ("Records 2.5 seconds and shows how loud it was. Nothing is played back, saved or sent.",
                    "Nimmt 2,5 Sekunden auf und zeigt, wie laut es war. Nichts wird abgespielt, gespeichert oder gesendet."),
    "ob_mic_button": ("Test the microphone", "Mikrofon testen"),
    "ob_mic_level": ("Loudness", "Lautstärke"),
    "ob_mic_ok": ("Sound received. The microphone works.", "Ton angekommen. Das Mikrofon funktioniert."),
    "ob_model_title": ("Speech model", "Sprachmodell"),
    "ob_model_ok": ("Ready: {model}", "Bereit: {model}"),
    "ob_model_none": ("No speech model is set up yet. Choose one under Speech recognition; it downloads once.",
                      "Noch kein Sprachmodell eingerichtet. Unter Spracherkennung eines wählen; es wird einmal geladen."),
    "ob_model_open": ("Open Speech recognition", "Spracherkennung öffnen"),
    "ob_try_title": ("Try it now", "Jetzt ausprobieren"),
    "ob_try_hint": ("Click into the field, hold {chord}, say a sentence and let go.",
                    "Ins Feld klicken, {chord} halten, einen Satz sagen und loslassen."),
    "ob_try_field": ("Practice field", "Übungsfeld"),
    # --- KI-Tier (lokale Modelle via Ollama) ---
    "nav_ai": ("AI", "KI"),
    "sec_ai": ("Local AI post-processing", "Lokale KI-Nachbearbeitung"),
    "ai_enable": ("Enable local AI (via Ollama)", "Lokale KI aktivieren (via Ollama)"),
    "ai_hint": ("Runs a local language model through Ollama to clean up or reformat your dictation. 100% on your machine — nothing is sent anywhere. Requires Ollama with a model installed. If it's unavailable, dictation just inserts the raw text.",
                "Nutzt ein lokales Sprachmodell über Ollama, um dein Diktat zu säubern oder umzuformen. 100% auf deinem Rechner — nichts wird gesendet. Benötigt Ollama mit installiertem Modell. Ist es nicht verfügbar, wird einfach der Rohtext eingefügt."),
    "ai_endpoint": ("Ollama address", "Ollama-Adresse"),
    "ai_endpoint_warning_title": ("Remote AI endpoint", "Fremder KI-Endpunkt"),
    "ai_endpoint_remote": ("Dictated text will be sent to this remote endpoint. Continue?", "Diktierter Text wird an diesen fremden Endpunkt gesendet. Fortfahren?"),
    "ai_endpoint_remote_plain": ("Dictated text will be sent unencrypted to this remote endpoint. Continue?", "Diktierter Text wird unverschluesselt an diesen fremden Endpunkt gesendet. Fortfahren?"),
    "ai_endpoint_invalid_title": ("Invalid AI address", "Ungültige KI-Adresse"),
    "ai_endpoint_invalid": ("Enter a complete http:// or https:// address.", "Bitte eine vollständige http://- oder https://-Adresse eingeben."),
    "ai_model": ("Model", "Modell"),
    "ai_refresh": ("Refresh", "Aktualisieren"),
    "ai_no_models": ("No models found — install one, e.g. `ollama pull llama3.2:3b`.",
                     "Keine Modelle gefunden — installiere eines, z. B. `ollama pull llama3.2:3b`."),
    "ai_unavailable": ("Ollama not reachable at this address.",
                       "Ollama unter dieser Adresse nicht erreichbar."),
    "ai_post_enable": ("Auto-clean every dictation", "Jedes Diktat automatisch säubern"),
    "ai_post_mode": ("Auto mode", "Auto-Modus"),
    "ai_voice_enable": ("Voice modes (say “as an email”, “as a list”…)",
                        "Sprachmodi („als E-Mail“, „als Liste“…)"),
    "ai_voice_hint": ("Start a dictation with a phrase like “as an email” or “make it concise” to reshape just that dictation.",
                      "Beginne ein Diktat mit „als E-Mail“ oder „kürzer“, um nur dieses Diktat umzuformen."),
    # --- Diktat bereinigen (cleanup.py, B19) ---
    "sec_cleanup": ("Clean up dictation", "Diktat bereinigen"),
    "cleanup_enable": ("Clean up each dictation", "Jedes Diktat bereinigen"),
    "cleanup_level": ("Level", "Stufe"),
    "cleanup_level_aus": ("Off", "Aus"),
    "cleanup_level_leicht": ("Light – fillers and repeats, without AI", "Leicht – Füllwörter und Wiederholungen, ohne KI"),
    "cleanup_level_mittel": ("Medium – plus careful edits by the local AI", "Mittel – dazu vorsichtige Überarbeitung durch die lokale KI"),
    "cleanup_level_stark": ("Strong – more editing by the local AI", "Stark – stärkere Überarbeitung durch die lokale KI"),
    "cleanup_hint": ("If the cleanup fails, takes too long or changes numbers, names, links or the meaning, Quassel inserts your dictation as spoken (raw text) instead. Medium and Strong use the AI address above through /v1/chat/completions (Ollama, llama-server, LM Studio); the model list above comes from Ollama only.",
                     "Scheitert die Bereinigung, dauert sie zu lange oder ändert sie Zahlen, Namen, Links oder den Sinn, fügt Quassel das Diktat so ein, wie es gesprochen wurde (Rohtext). Mittel und Stark nutzen die KI-Adresse oben über /v1/chat/completions (Ollama, llama-server, LM Studio); die Modellliste oben kommt nur von Ollama."),
    "raw_back": ("Raw text back", "Rohtext zurück"),
    "raw_back_hint": ("Puts the unedited text of your last dictation on the clipboard. It is kept only in memory and is gone when Quassel quits.",
                      "Legt den unbearbeiteten Text des letzten Diktats in die Zwischenablage. Er liegt nur im Arbeitsspeicher und ist weg, wenn Quassel endet."),
    "raw_back_sent": ("Requested – the pill shows the result.", "Angefordert – die Pille zeigt das Ergebnis."),
    "raw_back_unavailable": ("Quassel isn't running, so there is no last dictation.", "Quassel läuft nicht, es gibt kein letztes Diktat."),
    "raw_back_done": ("Raw text is on the clipboard – press Ctrl+V", "Rohtext liegt in der Zwischenablage – Strg+V drücken"),
    "raw_back_done_mac": ("Raw text is on the clipboard – press ⌘V", "Rohtext liegt in der Zwischenablage – ⌘V drücken"),
    "raw_back_none": ("No raw text from the last dictation (it was not changed by the cleanup).",
                      "Kein Rohtext vom letzten Diktat (die Bereinigung hat es nicht verändert)."),
    "raw_back_failed": ("Couldn't put the raw text on the clipboard.", "Rohtext ließ sich nicht in die Zwischenablage legen."),
    "sec_ai_modes": ("Custom AI modes", "Eigene KI-Modi"),
    "ai_modes_hint": ("One per line as name=instruction, e.g. tweet=Rewrite as a punchy tweet under 280 characters. Use the name as a voice phrase too.",
                      "Eine pro Zeile als name=anweisung, z. B. tweet=Schreibe als knackigen Tweet unter 280 Zeichen. Der Name funktioniert auch als Sprachphrase."),
    "ai_test": ("Test", "Test"),
    "ai_testing": ("Testing…", "Teste…"),
    "ai_test_ok": ("Works: {text}", "Funktioniert: {text}"),
    "ai_test_fail": ("No response — is the model installed and Ollama running?",
                     "Keine Antwort — ist das Modell installiert und Ollama gestartet?"),
    "ai_mode_cleanup": ("Clean up (filler, grammar)", "Säubern (Füllwörter, Grammatik)"),
    "ai_mode_email": ("Format as email", "Als E-Mail formatieren"),
    "ai_mode_bullets": ("Bullet list", "Aufzählung"),
    "ai_mode_paragraphs": ("Tidy paragraphs", "Saubere Absätze"),
    "ai_mode_formal": ("Make formal", "Förmlich machen"),
    "ai_mode_concise": ("Make concise", "Prägnant kürzen"),
    # --- Statistik-Seite (visuell) ---
    "nav_stats": ("Statistics", "Statistik"),
    "stats_total_words": ("Words dictated", "Diktierte Wörter"),
    "stats_time_spoken": ("Time spoken", "Gesprochene Zeit"),
    "stats_words_today": ("Words today", "Wörter heute"),
    "stats_saved": ("Typing time saved", "Gesparte Tippzeit"),
    "stats_chart": ("Words per day (last 14 days)", "Wörter pro Tag (letzte 14 Tage)"),
    "stats_chart_empty": ("Dictate something to see your daily activity here.",
                          "Diktiere etwas, um hier deine tägliche Aktivität zu sehen."),
}

_lang = None


def set_language(lang):
    global _lang
    _lang = lang if lang in SUPPORTED else None


def _tag(loc):
    """'de_DE.UTF-8', 'de-CH', 'German_Germany' -> 'de'; unbekannt -> ''."""
    loc = (loc or "").strip().lower()
    if loc.startswith("german"):            # Windows: locale.getlocale()
        return "de"
    if loc.startswith("english"):
        return "en"
    code = loc.replace("-", "_").split("_")[0].split(".")[0]
    return code if code in SUPPORTED else ""


def _parse_apple_languages(out):
    """Ausgabe von `defaults read -g AppleLanguages` -> ['de-DE', 'en-US']."""
    langs = []
    for line in out.splitlines():
        item = line.strip().strip("(),").strip().strip('"')
        if item:
            langs.append(item)
    return langs


def _langid_to_tag(langid):
    """Windows-LANGID -> 'de'/'en'/'' (Primärsprache in den unteren 10 Bit)."""
    return {0x07: "de", 0x09: "en"}.get(langid & 0x3FF, "")


def _query_os_ui_languages():
    """Sprachen der Systemoberfläche in Vorzugsreihenfolge.

    macOS: ein Start aus dem Finder setzt kein LANG, die Wahl in den
    Systemeinstellungen steht nur in AppleLanguages (NSLocale). Windows:
    GetUserDefaultUILanguage. Linux: nichts — dort gilt LANG."""
    if sys.platform == "darwin":
        try:
            from Foundation import NSLocale
            return [str(x) for x in NSLocale.preferredLanguages()]
        except Exception:  # noqa: BLE001 — ohne pyobjc über defaults
            pass
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                             capture_output=True, text=True, timeout=2,
                             check=False).stdout
        return _parse_apple_languages(out)
    if os.name == "nt":
        import ctypes
        tag = _langid_to_tag(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        return [tag] if tag else []
    return []


@functools.lru_cache(maxsize=1)
def _os_ui_languages():
    """Einmal je Prozess abfragen: tr() läuft bei jedem Neuzeichnen."""
    try:
        return tuple(_query_os_ui_languages())
    except Exception:  # noqa: BLE001 — Erkennung darf die App nie stoppen
        return ()


def current():
    if _lang:
        return _lang
    for var in ("LC_ALL", "LC_MESSAGES"):
        if os.environ.get(var):
            return _tag(os.environ[var]) or "en"
    for lang in _os_ui_languages():
        if _tag(lang):
            return _tag(lang)
    loc = os.environ.get("LANG") or ""
    if not loc:
        try:
            loc = locale.getlocale()[0] or ""
        except ValueError:
            loc = ""
    return _tag(loc) or "en"


def tr(key, **kw):
    if IS_MAC and key + "_mac" in STRINGS:
        key += "_mac"
    en, de = STRINGS.get(key, (key, key))
    s = de if current() == "de" else en
    return s.format(**kw) if kw else s
