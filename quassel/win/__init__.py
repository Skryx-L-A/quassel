"""Quassel für Windows — Ein-Prozess-App (Tray + Pille + Hotkey-Hook).

Architektur: Auf Windows gibt es kein systemd; stattdessen läuft alles in
einem Qt-Prozess: keyboard-Hook (hook.py), Aufnahme (audio_win.py),
whisper-server.exe-Verwaltung (server.py), Einfügen (paste.py),
Pille + Tray + Einstellungen (app.py). Geteilte Logik (config, i18n,
textproc, whisperclient) kommt aus dem quassel-Hauptpaket.
"""
