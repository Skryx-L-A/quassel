cask "quassel" do
  version "2.5.0"
  # SHA256 des von scripts/build_mac_dmg.sh gebauten dist/Quassel-macOS-arm64.dmg.
  # Platzhalter aus einem lokalen Entwickler-Build — der Orchestrator ersetzt ihn
  # beim Release durch die SHA256 des tatsächlich veröffentlichten DMG-Assets.
  sha256 "6cd67de3c9915b121e12774d374144ac16a24fb4bd1c843228fe0619fcbe7b82"

  url "https://github.com/Skryx-L-A/quassel/releases/download/v#{version}/Quassel-macOS-arm64.dmg"
  name "Quassel"
  desc "Fully local, system-wide voice typing (Metal-accelerated whisper.cpp)"
  homepage "https://github.com/Skryx-L-A/quassel"

  depends_on macos: :big_sur
  depends_on arch: :arm64

  # ffmpeg-Abhängigkeit NICHT aktivieren, solange die Messung läuft, ob die App
  # ffmpeg zur Laufzeit überhaupt noch braucht (parallele Untersuchung, Stand
  # 2026-07-28). Erst eintragen, wenn diese Messung "ja" ergibt:
  # depends_on formula: "ffmpeg"

  app "Quassel.app"

  # Selbstsigniert ("Quassel Dev"), nicht notarisiert (kein Apple-Developer-
  # Account) — ohne das entfernte Quarantäne-Flag würde Gatekeeper den ersten
  # Start blockieren.
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", "#{appdir}/Quassel.app"],
                   sudo: false
  end

  uninstall launchctl: "de.skryx.quassel",
            quit:      "de.skryx.quassel"

  zap trash: [
    "~/Library/Application Support/Quassel",
    "~/Library/LaunchAgents/de.skryx.quassel.plist",
    "~/Library/Logs/Quassel",
    "~/Library/Preferences/de.skryx.quassel.plist",
  ]
end
