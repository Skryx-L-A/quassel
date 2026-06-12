# PyInstaller-Spezifikation für Quassel (Windows).
# Bauen:  pip install pyinstaller pyside6 sounddevice
#         pyinstaller windows/quassel.spec   (aus beliebigem Verzeichnis)
# Ergebnis: dist/Quassel/Quassel.exe (whisper.cpp + Modell lädt die App
# beim ersten Start selbst herunter — die exe bleibt klein).
import os

# SPECPATH = Ordner dieser Datei (windows/); das Repo liegt eine Ebene höher
repo = os.path.dirname(SPECPATH)

block_cipher = None

a = Analysis(
    [os.path.join(SPECPATH, 'quassel_win_main.py')],
    pathex=[repo],
    binaries=[],
    datas=[(os.path.join(repo, 'assets', 'quassel.png'), 'assets'),
           (os.path.join(repo, 'assets', 'quassel.ico'), 'assets'),
           (os.path.join(repo, 'assets', 'quassel.svg'), 'assets')],
    hiddenimports=['sounddevice', 'quassel', 'quassel.win.app',
                   'quassel.center'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='Quassel',
    console=False,
    icon=os.path.join(repo, 'assets', 'quassel.ico'),
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name='Quassel')
