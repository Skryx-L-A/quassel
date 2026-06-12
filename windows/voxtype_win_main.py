"""Einstiegspunkt für die Windows-exe (von PyInstaller gebündelt)."""
import os
import sys

# Im gebündelten Zustand liegt das voxtype-Paket neben dieser Datei
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voxtype.win.app import main

if __name__ == "__main__":
    main()
