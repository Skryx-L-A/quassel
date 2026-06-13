# PyInstaller-Spezifikation für Quassel (Windows).
# Bauen:  pip install pyinstaller pyside6 sounddevice pycaw winrt-Windows.Media.Control winrt-Windows.Foundation
#         pyinstaller windows/quassel.spec   (aus beliebigem Verzeichnis)
# Ergebnis: dist/Quassel/Quassel.exe (whisper.cpp + Modell lädt die App
# beim ersten Start selbst herunter — die exe bleibt klein).
import os

from PyInstaller.utils.hooks import (
    collect_submodules, collect_dynamic_libs, collect_data_files)

# SPECPATH = Ordner dieser Datei (windows/); das Repo liegt eine Ebene höher
repo = os.path.dirname(SPECPATH)

block_cipher = None

# Audio-Ducking-Backends fest einbündeln, damit das Offline-Komplettpaket sie
# OHNE Internet enthält: pycaw (+comtypes) fuer den Master-Mute, die winrt-
# Projektion (mit der _winrt-Erweiterung) fuer SMTC-Mediensteuerung.
audio_hidden = (['quassel.mediacontrol', 'quassel.win.audioctl',
                 'comtypes', 'pycaw']
                + collect_submodules('comtypes')
                + collect_submodules('pycaw')
                + collect_submodules('winrt'))
audio_binaries = collect_dynamic_libs('winrt') + collect_dynamic_libs('comtypes')
audio_datas = collect_data_files('winrt')

a = Analysis(
    [os.path.join(SPECPATH, 'quassel_win_main.py')],
    pathex=[repo],
    binaries=audio_binaries,
    datas=[(os.path.join(repo, 'assets', 'quassel.png'), 'assets'),
           (os.path.join(repo, 'assets', 'quassel.ico'), 'assets'),
           (os.path.join(repo, 'assets', 'quassel.svg'), 'assets')] + audio_datas,
    hiddenimports=['sounddevice', 'quassel', 'quassel.win.app',
                   'quassel.center'] + audio_hidden,
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
