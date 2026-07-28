# Homebrew-Cask für Quassel

Dieser Ordner ist die Quelle für den separaten Tap-Repo `Skryx-L-A/homebrew-quassel`.
Homebrew erlaubt Casks nur aus einem Repo, dessen Name mit `homebrew-` beginnt —
deshalb kein Cask im Haupt-Repo, sondern ein eigenes.

## Aufbau des Tap-Repos

```
homebrew-quassel/
└── Casks/
    └── quassel.rb
```

Reiner Cask-Ordner, kein `Formula/`. `Casks/quassel.rb` ist wortgleich mit
`packaging/homebrew/Casks/quassel.rb` in diesem Repo — dort gepflegt, ins
Tap-Repo bei jedem Release kopiert (kein Submodule, kein Sync-Skript nötig,
eine Datei).

Einmalig einrichten (macht der Orchestrator, nicht dieser Worker):

```sh
brew tap-new skryx-l-a/quassel   # legt den Repo-Rohbau lokal an
# quassel.rb reinkopieren, dann als Skryx-L-A/homebrew-quassel auf GitHub pushen
```

Nutzer installieren danach ohne separates `brew tap`:

```sh
brew install --cask skryx-l-a/quassel/quassel
```

## Bei jedem Release aktualisieren

`Casks/quassel.rb` hat genau drei Stellen, die sich pro Release ändern:

1. **`version`** — neue Quassel-Version (`quassel/__init__.py:__version__`,
   identisch zu `CFBundleShortVersionString` im gebauten `.app`).
2. **`sha256`** — SHA256 des veröffentlichten `Quassel-macOS-arm64.dmg`-Assets,
   Ausgabe von `scripts/build_mac_dmg.sh` (oder `shasum -a 256 <dmg>`). Der
   Wert im Repo ist ein Platzhalter aus einem lokalen Entwickler-Build — **nie
   ungeprüft übernehmen**, immer gegen das tatsächlich hochgeladene
   Release-Asset neu berechnen.
3. **`url`** trägt die Version bereits automatisch über `v#{version}` im
   Tag-Pfad (`releases/download/v#{version}/Quassel-macOS-arm64.dmg`) — an der
   Zeile selbst ist bei einem reinen Versions-Bump nichts zu ändern, nur
   `version` oben muss stimmen.

Danach prüfen:

```sh
brew style --cask Casks/quassel.rb   # aus einem echten Tap-Checkout heraus
```

(`brew style` verweigert die Prüfung außerhalb eines registrierten Taps — zum
Testen notfalls mit `brew tap-new` einen Wegwerf-Tap anlegen, Datei
reinkopieren, prüfen, mit `brew untap` wieder entfernen.)

## ffmpeg-Abhängigkeit

Aktuell **nicht** als `depends_on formula: "ffmpeg"` eingetragen (auskommentiert
in `quassel.rb`) — es läuft eine separate Messung, ob die App ffmpeg zur
Laufzeit überhaupt noch braucht. Ergibt die Messung "ja", die Zeile aktivieren
und diesen Absatz entfernen.

## uninstall / zap

- `uninstall launchctl:` + `quit:` beenden den LaunchAgent (`de.skryx.quassel`,
  siehe `quassel/center.py:mac_autostart_set`) und die laufende App.
- `zap trash:` räumt zusätzlich Application-Support-Daten (Modelle, History),
  Logs, Preferences und den LaunchAgent-Plist weg (`brew uninstall --zap`).
