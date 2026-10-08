---
name: Bug report
about: Something doesn't work / Etwas funktioniert nicht
labels: bug
---

<!-- Please check docs/TROUBLESHOOTING.md first: it explains the messages Quassel shows. -->

**What happened? / Was ist passiert?**

**What did you expect? / Was hast du erwartet?**

**Steps to reproduce / Schritte zum Nachstellen**
1.

**Quassel**
- Version (control center → About, or the version in the first log line):
- Installed how (Homebrew cask, DMG, one-line installer, `install.sh`, Windows setup, from source):
- Mode when it happened (hold, hands-free, wake word, streaming, AI or clean-up on):

**System** (fill in the part that applies)
- Platform and version (Linux distro / macOS version / Windows 10 or 11):
- Linux: desktop (KDE/GNOME/…), Wayland or X11, PipeWire or PulseAudio:
- macOS: chip (Apple Silicon model), permissions granted (Microphone, Accessibility, Input Monitoring):
- Windows: installed or portable, GPU driver:
- GPU (NVIDIA/AMD/Intel/none) and the speech model you use:
- Microphone (built-in, USB, Bluetooth):

**Logs** (the part around the time of the problem; they do not contain dictated text)

- Linux: `journalctl --user -u quasseld -u quassel-server -u quassel-ydotoold -n 80`
- macOS: `~/Library/Logs/Quassel/daemon.log` and `~/Library/Application Support/Quassel/whisper-server.log`
- Windows: `%LOCALAPPDATA%\Quassel\debug.log`, `crash.log` and `whisper-server.log`

```
```

**Anything else? / Sonst noch etwas?**
Screenshots or the message shown in the pill help. Please do not attach
recordings or text you want to keep private.
