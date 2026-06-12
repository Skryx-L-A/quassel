# PyInstaller-Spezifikation für VoxType (Windows).
# Bauen:  pip install pyinstaller pyside6 sounddevice
#         pyinstaller windows/voxtype.spec   (aus beliebigem Verzeichnis)
# Ergebnis: dist/VoxType/VoxType.exe (whisper.cpp + Modell lädt die App
# beim ersten Start selbst herunter — die exe bleibt klein).
import os

# SPECPATH = Ordner dieser Datei (windows/); das Repo liegt eine Ebene höher
repo = os.path.dirname(SPECPATH)

block_cipher = None

a = Analysis(
    [os.path.join(SPECPATH, 'voxtype_win_main.py')],
    pathex=[repo],
    binaries=[],
    datas=[(os.path.join(repo, 'assets', 'voxtype.png'), 'assets'),
           (os.path.join(repo, 'assets', 'voxtype.ico'), 'assets'),
           (os.path.join(repo, 'assets', 'voxtype.svg'), 'assets')],
    hiddenimports=['sounddevice', 'voxtype', 'voxtype.win.app',
                   'voxtype.center'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='VoxType',
    console=False,
    icon=os.path.join(repo, 'assets', 'voxtype.ico'),
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name='VoxType')
