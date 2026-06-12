# PyInstaller-Spezifikation für VoxType (Windows).
# Bauen:  pip install pyinstaller pyside6 sounddevice
#         pyinstaller windows/voxtype.spec
# Ergebnis: dist/VoxType/VoxType.exe (whisper.cpp + Modell lädt die App
# beim ersten Start selbst herunter — die exe bleibt klein).
import os

block_cipher = None
root = os.path.abspath(os.getcwd())

a = Analysis(
    ['voxtype_win_main.py'],
    pathex=[root],
    binaries=[],
    datas=[('../assets/voxtype.png', 'assets'),
           ('../assets/voxtype.ico', 'assets'),
           ('../assets/voxtype.svg', 'assets')],
    hiddenimports=['sounddevice', '_sounddevice_data'],
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
    icon='../assets/voxtype.ico',
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name='VoxType')
