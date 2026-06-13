Quassel — Offline-Komplettpaket für Linux (x86_64)
Quassel — offline all-in-one package for Linux (x86_64)
========================================================

DEUTSCH
-------
Dieses Paket bringt ALLES mit: Python + Qt-Oberfläche, die Spracherkennungs-
Engine (CPU UND CUDA/NVIDIA), ALLE Sprachmodelle (tiny, base, small, medium,
large-v3-turbo), Tastatur-Injektion (ydotool) und Zwischenablage-Werkzeug.
Es wird KEIN Internet benötigt — auch nicht beim ersten Start.

Installation:
  1. Falls in mehreren Teilen geladen: alle Teile in denselben Ordner legen und
     entpacken (siehe "Mehrteiliger Download" unten).
  2. Ein Terminal im entpackten Ordner öffnen und ausführen:
         ./install.sh
  3. Einmalig nach Passwort gefragt (Tastatur-/uinput-Berechtigung). Wenn der
     Installer es verlangt: einmal ab- und wieder anmelden.
  4. App „Quassel" im Startmenü öffnen und einschalten.

Benutzung:
  - In ein Textfeld klicken, dann:
      Strg+Meta HALTEN  -> sprechen -> loslassen -> Text erscheint
      Strg+Meta 2x TIPPEN -> freihändig sprechen -> 1x drücken -> Text erscheint

Beim ersten Start wird je nach Hardware automatisch ein passendes Modell und
die passende Engine gewählt (NVIDIA -> CUDA, sonst CPU). Alles ist bereits
enthalten; in den Einstellungen kann jederzeit ein anderes Modell gewählt
werden, ohne etwas herunterzuladen.

Voraussetzungen am Zielsystem: ein laufender Desktop mit Audio (PipeWire oder
PulseAudio — auf jedem Desktop vorhanden). Für die CUDA-Engine wird ein
installierter NVIDIA-Treiber benötigt (die CUDA-Laufzeit selbst ist im Paket).
Die schwebende Pille sitzt unter X11 exakt unten-mittig; unter Wayland hängt die
genaue Position vom Compositor ab (das Diktieren funktioniert unabhängig davon).

Deinstallation:   ./uninstall.sh
Paket verschieben: nach dem Verschieben einfach erneut ./install.sh ausführen.


ENGLISH
-------
This package contains EVERYTHING: Python + Qt UI, the speech engine (CPU AND
CUDA/NVIDIA), ALL language models (tiny, base, small, medium, large-v3-turbo),
keystroke injection (ydotool) and a clipboard tool. NO internet is required —
not even on first run.

Install:
  1. If downloaded in parts: put all parts in the same folder and extract
     (see "Multi-part download" below).
  2. Open a terminal in the extracted folder and run:
         ./install.sh
  3. You will be asked for your password once (keyboard/uinput permission). If
     the installer asks, log out and back in once.
  4. Open the "Quassel" app from your launcher and turn it on.

Use:
  - Click into any text field, then:
      Hold Ctrl+Meta -> speak -> release -> text appears
      Double-tap Ctrl+Meta -> speak hands-free -> press once -> text appears

On first run a suitable model and engine are chosen automatically based on your
hardware (NVIDIA -> CUDA, otherwise CPU). Everything is already included; you can
switch models any time in settings without downloading anything.

Target requirements: a running desktop with audio (PipeWire or PulseAudio, present
on every desktop). The CUDA engine needs an installed NVIDIA driver (the CUDA
runtime itself ships in this package). The floating pill is anchored bottom-center
on X11; on Wayland its exact position depends on the compositor (dictation works
regardless).

Uninstall:   ./uninstall.sh


Mehrteiliger Download / Multi-part download
-------------------------------------------
Wenn das Paket als mehrere Teile vorliegt
(quassel-offline-linux-x86_64.tar.gz.part-aa, .part-ab, ...), alle Teile in
denselben Ordner legen und zusammenfügen + entpacken:

    cat quassel-offline-linux-x86_64.tar.gz.part-* | tar xzf -
    cd quassel-linux-x86_64
    ./install.sh
