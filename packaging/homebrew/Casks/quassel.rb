cask "quassel" do
  version "2.9.0"
  # SHA256 des veröffentlichten Release-Assets (nicht eines lokalen Builds).
  # Bei jedem Release neu setzen: scripts/build_mac_dmg.sh gibt sie aus, und sie
  # liegt als Quassel-macOS-arm64.dmg.sha256 am Release.
  sha256 "6c3f05eba20bec511f9c03aebd303e542c270693f721b52b424c225d50671ef9"

  url "https://github.com/Skryx-L-A/quassel/releases/download/v#{version}/Quassel-macOS-arm64.dmg"
  name "Quassel"
  desc "Fully local, system-wide voice typing (Metal-accelerated whisper.cpp)"
  homepage "https://github.com/Skryx-L-A/quassel"

  depends_on macos: :big_sur
  depends_on arch: :arm64

  # Keine ffmpeg-Abhängigkeit: seit 2.5.0 nimmt die App über sounddevice/
  # CoreAudio auf (CHANGELOG 2.5.0); ffmpeg dient nur noch dazu, vorhandene
  # Nicht-WAV-Audiodateien umzuwandeln. Wer das braucht, installiert es selbst.
  # depends_on formula: "ffmpeg"

  app "Quassel.app"

  # Selbstsigniert ("Quassel Dev"), nicht notarisiert (kein Apple-Developer-
  # Account) — ohne das entfernte Quarantäne-Flag würde Gatekeeper den ersten
  # Start blockieren.
  # Homebrew 7 ersetzt den Ruby-Block `postflight` durch deklarative Schritte.
  postflight_steps do
    run "/usr/bin/xattr", args: ["-dr", "com.apple.quarantine", "{{appdir}}/Quassel.app"]
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
