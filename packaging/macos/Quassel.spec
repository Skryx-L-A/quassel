# PyInstaller-Spezifikation für Quassel (macOS, onedir-.app).
# Bauen:  scripts/build_mac_app.sh   (oder: pyinstaller packaging/macos/Quassel.spec)
# Ergebnis: dist/Quassel.app — whisper-server (Metal) + dylibs liegen im Bundle
# unter Contents/Resources/whisper/; die ggml-Modelle bleiben AUSSERHALB
# (~/Library/Application Support/Quassel/models, lädt die App selbst herunter).
import os
import re

# SPECPATH = Ordner dieser Datei (packaging/macos/); Repo zwei Ebenen höher
repo = os.path.dirname(os.path.dirname(SPECPATH))

with open(os.path.join(repo, "quassel", "__init__.py"), encoding="utf-8") as f:
    version = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)

# whisper-server + genau die dylibs, die er per @rpath lädt (sonames; die
# Symlinks im Build-Ordner zeigen auf die realen versionierten Dateien —
# PyInstaller kopiert das Ziel unter dem Soname-Namen ins Bundle).
whisper_bin = os.path.join(repo, "vendor", "whisper.cpp", "build", "bin")
whisper_files = ["whisper-server", "libwhisper.1.dylib", "libggml.0.dylib",
                 "libggml-base.0.dylib", "libggml-cpu.0.dylib",
                 "libggml-blas.0.dylib", "libggml-metal.0.dylib"]
whisper_binaries = [(os.path.join(whisper_bin, f), "whisper")
                    for f in whisper_files]

sounds = os.path.join(repo, "assets", "sounds")
icons = os.path.join(repo, "assets", "icons")
datas = [(os.path.join(repo, "assets", "quassel.svg"), "assets"),
         (os.path.join(repo, "assets", "quassel.png"), "assets"),
         (os.path.join(sounds, "start.wav"), "assets/sounds"),
         (os.path.join(sounds, "stop.wav"), "assets/sounds")]
datas += [(os.path.join(icons, f), "assets/icons")
          for f in sorted(os.listdir(icons)) if f.endswith(".png")]

a = Analysis(
    [os.path.join(SPECPATH, "quassel_mac_main.py")],
    pathex=[repo],
    binaries=whisper_binaries,
    datas=datas,
    # daemon/center werden erst zur Laufzeit als Subkommando importiert;
    # pyobjc-Module (AppKit/Quartz) importiert der Code lazy in Funktionen.
    hiddenimports=["sounddevice", "quassel", "quassel.mac_app",
                   "quassel.daemon", "quassel.center",
                   "AppKit", "Quartz"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Quassel",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Quassel")
app = BUNDLE(
    coll,
    name="Quassel.app",
    icon=None,
    bundle_identifier="de.skryx.quassel",
    info_plist={
        "CFBundleName": "Quassel",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        # Menüleisten-App: kein Dock-Icon, kein App-Switcher-Eintrag
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription":
            "Quassel nimmt deine Stimme über das Mikrofon auf und wandelt sie "
            "lokal auf diesem Mac in Text um (Diktat). Es werden keine "
            "Audiodaten ins Internet gesendet.",
    },
)
