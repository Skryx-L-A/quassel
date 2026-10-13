"""Quassel-Daemon: Modifier-Chord-Erkennung (Standard Strg+Meta) über evdev.

Modi:
  * Halten:     Chord halten, sprechen, loslassen -> Text wird eingefügt
  * Doppeltipp: Chord 2× kurz tippen -> freihändig sprechen;
                1× drücken -> Text wird eingefügt

Während der Aufnahme entstehen alle ~2 s Teiltranskripte (letzte 15 s Audio)
für die Live-Vorschau in der Pille (state.json).

Der Ablauf eines Diktats selbst (Aufnahme, Transkription, Einfügen, Rettung,
Not-Aus) steht in dictation.py und wird mit der Windows-App geteilt. Hier
bleiben die Plattform-Anbindung für Linux und macOS und die beiden
Hotkey-Schleifen.
"""
import faulthandler
import json
import os
import secrets
import select
import signal
import struct
import subprocess
import sys
import threading
import time

from . import __version__
# textproc und whisperclient nutzt dictation.py; hier mit importiert, damit
# Tests sie über dieses Modul ersetzen können.
from . import beep, config, dictation, fsutil, i18n, profile, profiles, selfheal, textproc, whisperclient  # noqa: F401
from .dictation import (  # noqa: F401 — Namen bleiben hier ansprechbar
    LEAD_TRIM_MS, LEAD_TRIM_NOVAD_BT_MS, PARTIAL_EVERY, PARTIAL_MAX_WAIT,
    PARTIAL_WINDOW, RESCUE_GLOB, RESCUE_KEEP, RESCUE_NAME, SERVER_WAIT_COLD,
    SERVER_WAIT_FINISH, SLOW_STOP, TAIL_PAD_BT_MS, WAKE_RAW,
    PartialLoop, postprocess_transcript)
from .mediacontrol import AudioDucker
from .audio import Recorder, mac_backend, record_command, wav_from_raw
from .config import CHORDS
from .i18n import tr
from .platform import (mic_is_bluetooth, notify, paste, send_backspaces,
                       send_enter, type_chunk, streaming_begin, streaming_restore)
from .state import (PARTWAV, RUNDIR, WAV, level_set, state_clear_text,  # noqa: F401
                    state_set)

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 1
KEY_PRESS = 1

MAX_RECORD = 300       # s: Sicherheitslimit je Aufnahme (Halten und Freihand)
# Zustände der Linux-Schleife, in denen eine Aufnahme läuft.
RECORDING_STATES = ("hold", "toggle_armed", "toggle")
RESCAN_EVERY = 5       # s: /dev/input auf neue Tastaturen prüfen
AUTOSTOP_POLL = 0.5     # s: macOS-Wächter, solange ein Freihand-Auto-Stopp scharf ist
PANIC_EXIT_WAIT = 5.0  # s: so lange wartet der Not-Aus auf das Ende einer Aktion
PANIC_SIGNAL_WAIT = 0.5  # s: so lange wartet der SIGUSR2-Handler auf den Not-Aus-Thread
# Der Ereignis-Thread führt den synchronen Teil der Aktionen selbst aus, und
# darin steckt osascript (Benachrichtigung, Ducking) mit je 5 s Frist. Der Wert
# liegt darüber, damit ein zäher, aber normaler Start keinen Fehlalarm samt
# Thread-Dump in genau das Log kippt, das im Ernstfall gelesen wird.
EVENT_STALL = 10.0     # s: so lange darf der Ereignis-Thread höchstens schweigen
ACTION_STALL = 60.0    # s: so lange darf eine Hotkey-Aktion höchstens laufen
DEAD_STALL = 30.0      # s: danach ist der Prozess verloren -> Selbstneustart
# Beendigungscode für den Selbstneustart. Die Aufsicht in mac_app.py startet den
# Daemon danach neu; 0 wäre ein normales Ende, 1 ein Fehler.
RESTART_EXIT = 3
SERVER_RESTART_REQUEST = os.path.join(RUNDIR, "server-restart.request")
SERVER_RESTART_ACK = os.path.join(RUNDIR, "server-restart.ack")
# Nachlauf; hier statt nur in dictation.py, damit Tests ihn abschalten können.
TAIL_PAD_MS = dictation.TAIL_PAD_MS


def log(msg):
    # Zeitstempel: im Log der App (~/Library/Logs/Quassel/daemon.log) ließe
    # sich sonst weder ein Neustart noch die Dauer eines Hängers ablesen.
    print("%s %s" % (time.strftime("%H:%M:%S"), msg), file=sys.stderr, flush=True)


def clip_copy(text):
    """Zwischenablage des Plattform-Backends (quassel.platform reicht nur die
    Diktat-Funktionen durch). Modulfunktion, damit Tests sie ersetzen können."""
    import importlib
    from . import platform as platform_mod
    return importlib.import_module(platform_mod._backend_module_name()).clip_copy(text)


def clip_read():
    """Text der Zwischenablage über das Plattform-Backend; None, wenn das
    Backend keine Lesefunktion hat."""
    import importlib
    from . import platform as platform_mod
    backend = importlib.import_module(platform_mod._backend_module_name())
    read = getattr(backend, "clip_read", None)
    return read() if read is not None else None


def _restart_linux_server():
    """Restart only an active user unit bound to this process's profile."""
    name = profile.name()
    service = profile.service()
    try:
        shown = subprocess.run(
            ["systemctl", "--user", "show", "-p", "ActiveState", "-p", "Environment",
             service], capture_output=True, text=True, timeout=3, check=False)
        if shown.returncode != 0:
            return False
        values = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
        if values.get("ActiveState") != "active":
            return False
        assigned = [part.split("=", 1)[1] for part in values.get("Environment", "").split()
                    if part.startswith("QUASSEL_PROFILE=")]
        if (assigned[0] if assigned else "") != name:
            return False
        restarted = subprocess.run(["systemctl", "--user", "restart", service],
                                   capture_output=True, timeout=8, check=False)
        return restarted.returncode == 0 and _wait_functional_server()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def _wait_functional_server(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if selfheal.functional_probe(timeout=2):
            return True
        time.sleep(1)
    return False


def _request_mac_server_restart(timeout=45):
    """Ask the owning App process to restart its child, via this profile's run dir."""
    os.makedirs(RUNDIR, mode=0o700, exist_ok=True)
    token = secrets.token_hex(16)
    request = {"token": token, "daemon_pid": os.getpid(), "app_pid": os.getppid(),
               "profile": profile.name(), "created": time.time()}
    try:
        fsutil.atomic_write(SERVER_RESTART_REQUEST, json.dumps(request), mode=0o600)
    except OSError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(SERVER_RESTART_ACK, encoding="utf-8") as source:
                ack = json.load(source)
            if ack.get("token") == token:
                return ack.get("started") is True and _wait_functional_server()
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    return False


def _install_server_recovery():
    """Bind the shared one-retry path to this platform's owned server."""
    original = selfheal.recover
    restart = _request_mac_server_restart if sys.platform == "darwin" else _restart_linux_server
    selfheal.recover = lambda: original(restart=restart)


class Daemon(dictation.DictationCore):
    # Entprellung der Watchdog-Warnung: eine Meldung je Hänger, nicht alle 5 s.
    _stall_logged = False
    _listener = None            # MacHotkeyListener, nur im mac-Loop gesetzt
    # Prozessende ohne Aufräumen: ein reguläres sys.exit liefe beim Abbau der
    # Audio-Bibliothek erneut in den verklemmten CoreAudio-Mutex. Als Attribut,
    # damit Tests den Aufruf abfangen können, statt sich selbst zu beenden.
    _exit = os._exit

    def __init__(self):
        self.cfg = config.Cfg()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        beep.set_output(self.cfg.beep_output)
        self.rec = Recorder(preroll_ms=getattr(self.cfg, "preroll_ms", 0))
        self.partial = None
        self.last_paste_len = 0
        self.streamer = None        # aktiv nur im Freihand-Modus mit Streaming
        self._clip_backup = None
        self.ducker = AudioDucker()  # Musik pausieren / Ton stummschalten beim Diktieren
        self.wake = None             # WakeListener-Thread (nur wenn Wake-Word an)
        self._bt = False             # ist die aktive Aufnahmequelle Bluetooth?
        self._vad = False            # läuft serverseitig VAD? (dann Vorlauf nicht doppelt trimmen)
        self._linux_reset = threading.Event()
        profiles.prewarm()           # Per-App-Profile: AppKit nicht erst im ersten Diktat laden

    # ------------------------------------------------- Plattform-Anbindung
    # Jede Methode löst ihren Namen erst beim Aufruf in DIESEM Modul auf:
    # Tests ersetzen daemon.paste, daemon.state_set usw. und treffen damit
    # die gemeinsame Ablaufsteuerung.
    def _state(self, state, text=""):
        return state_set(state, text)

    def _clear_state_text(self, token):
        state_clear_text(token)

    def _notify(self, text):
        notify(text)

    def _log(self, msg):
        log(msg)

    def _paste(self, text, extra_delay=0.0):
        return paste(text, extra_delay) if extra_delay else paste(text)

    def _send_backspaces(self, n):
        send_backspaces(n)

    def _send_enter(self):
        send_enter()

    def _type_chunk(self, chunk):
        type_chunk(chunk)

    def _streaming_begin(self):
        return streaming_begin()

    def _streaming_restore(self, backup):
        streaming_restore(backup)

    def _wav_from_raw(self, data, path):
        wav_from_raw(data, path)

    def _new_partial(self):
        return PartialLoop(self.rec, self.cfg, self)

    def _rundir(self):
        return RUNDIR

    def _new_recorder(self, raw_path):
        return Recorder(raw_path=raw_path)

    def _mic_is_bluetooth(self):
        return mic_is_bluetooth(self.cfg.mic)

    def _clip_copy(self, text):
        return clip_copy(text)

    def _clip_read(self):
        return clip_read()

    def _level_target(self):
        # Pegel über state.json an die Pille (eigener Prozess); level_set
        # schreibt nur im Zustand "recording" und höchstens 15-mal je Sekunde.
        return lambda level: level_set(level)

    def _server_vad(self):
        return bool(config.read_serverenv().get("VAD_MODEL"))

    def _mic_error_text(self):
        """Warum ließ sich die Aufnahme nicht starten (H113)?"""
        if sys.platform == "darwin" and mac_backend() != "ffmpeg":
            # sounddevice-Pfad: es fehlt kein Programm, sondern ein
            # nutzbares Eingabegerät oder die Mikrofon-Freigabe.
            if dictation.mac_mic_permission() == "denied":
                return tr("err_mic_denied")
            return tr("err_mic_open")
        if record_command(self.cfg.mic) is None:
            tool = "ffmpeg" if sys.platform == "darwin" else "pw-record/parecord"
            return tr("err_recorder_missing", tool=tool)
        return tr("err_mic_open")

    def _tail_pad_ms(self):
        return TAIL_PAD_BT_MS if self._bt else TAIL_PAD_MS

    def _after_audio_released(self):
        self._restart_if_audio_poisoned()

    def _after_panic(self):
        self._restart_if_audio_poisoned(after_panic=True)

    def _reset_hotkey(self):
        if self._listener is not None:
            self._listener.force_reset()
        elif hasattr(self, "_linux_reset"):
            self._linux_reset.set()

    def panic_stop(self, *_args):
        # Sleep can arrive with keys held but no active recording. Reset the
        # Linux key table even when the shared panic path has nothing to stop.
        if sys.platform.startswith("linux") and hasattr(self, "_linux_reset"):
            self._linux_reset.set()
        return super().panic_stop(*_args)

    def _consume_linux_reset(self, pressed):
        if not self._linux_reset.is_set():
            return False
        self._linux_reset.clear()
        pressed.clear()
        return True

    def _action_running(self):
        if self._listener is not None and self._listener.current_action is not None:
            return True
        return super()._action_running()

    def _wake_transcribe(self, pcm):
        # Über die Modulnamen dieses Moduls, damit Tests sie ersetzen können.
        return dictation.wake_transcribe(self.cfg, pcm, dictation.scratch_wav("wake"),
                                         wav_from_raw, log)

    def _restart_if_audio_poisoned(self, after_panic=False):
        """Musste ein Aufnahme-Stream aufgegeben werden, steckt in CoreAudio ein
        Mutex fest, den auch der nächste Stream braucht: der Hänger wanderte dann
        nur vom Stoppen zum Starten. Der Prozess ist nicht mehr zu retten, also
        beendet er sich — die Aufsicht in mac_app.py startet ihn neu.

        Im normalen Ablauf wird das erst gerufen, wenn der Text schon eingefügt
        ist. Aus dem Not-Aus heraus (anderer Thread) wird zusätzlich gewartet,
        bis keine Aktion mehr läuft: sonst risse os._exit ein Einfügen mitten
        entzwei. Klappt das nicht, bleibt das Flag stehen und das nächste
        Diktat-Ende erledigt es."""
        if not getattr(self.rec, "stream_abandoned", False):
            return
        if after_panic and not self._wait_for_idle_action():
            log("panic: Aktion läuft noch -> Neustart erst nach ihrem Ende")
            return
        log("audio: Stream aufgegeben, CoreAudio verklemmt -> Daemon startet neu")
        notify(tr("audio_restart"))
        self._exit(RESTART_EXIT)

    def _wait_for_idle_action(self, deadline=None):
        """Warten, bis der Aktions-Thread frei ist (höchstens deadline Sekunden).
        True = frei."""
        deadline = PANIC_EXIT_WAIT if deadline is None else deadline
        listener = self._listener
        if listener is None:
            return True
        end = time.monotonic() + deadline
        while listener.current_action is not None:
            if time.monotonic() >= end:
                return False
            time.sleep(0.1)
        return True


    # ------------------------------------------------------------- Hauptloop
    def run(self):
        if sys.platform == "darwin":
            self._run_mac()
            return
        self._run_linux()

    def _run_mac(self):
        """macOS: CGEventTap (hotkey_mac.py) statt evdev-Read-Loop. Nutzt
        dieselbe ChordMachine wie der Windows-Hook -> identische Halten-/
        Doppeltipp-/Abbruch-Semantik, nur die Event-Quelle unterscheidet sich."""
        from .hotkey_mac import MacHotkeyListener, check_permissions
        # Prüft Lesen (Eingabeüberwachung) UND Senden (Bedienungshilfen);
        # benachrichtigt je fehlender Freigabe, degradiert nur, stürzt nie ab.
        missing = check_permissions(chord_name=self.cfg.chord)
        if missing:
            log("mac: fehlende TCC-Freigaben: " + ", ".join(missing))
        state_set("idle")
        notify(tr("ready"), 2000)
        self._install_panic_signal()
        self._sync_wakeword()
        listener = MacHotkeyListener(
            self.cfg.chord, self.cfg,
            on_start=self.start_recording, on_finish=self.finish,
            on_cancel=self.cancel_recording, on_handsfree=self.enable_streaming)
        self._listener = listener       # der Not-Aus setzt darüber zurück
        listener.start()
        while True:
            # Mit scharfem Auto-Stopp (Freihand) öfter hinsehen als alle 5 s.
            time.sleep(AUTOSTOP_POLL if self._autostop is not None else RESCAN_EVERY)
            if self.cfg.reload():
                i18n.set_language(None if self.cfg.ui_language == "auto"
                                  else self.cfg.ui_language)
                # Hotkey-/Timing-Änderungen aus den Einstellungen greifen
                # ohne Neustart (wie im Linux-Loop)
                listener.reconfigure(self.cfg.chord, self.cfg.hold_min,
                                     self.cfg.double_window)
            self._sync_wakeword()
            self._mac_watchdog(listener)

    def _linux_finish(self):
        """Diktat-Ende im Linux-Loop. Die Schleife liest die Tastatur
        einthreadig und wartet weiter auf den Rest, bevor sie das nächste
        Ereignis liest — die Reihenfolge bleibt also wie bisher. Gewartet wird
        aber auf dem Aktions-Thread mit Frist: hängt der Rest (wl-paste,
        Server), meldet der Watchdog das einmal samt Thread-Stacks, und der
        Not-Aus lässt ihn zurück, statt mit ihm stehen zu bleiben (D55)."""
        rest = self.finish()
        if rest is None:
            return True
        runner = self._runner
        if runner is None:
            rest()
            return True
        done = runner.run_blocking("finish", rest, on_stall=self._linux_stall,
                                   stall_after=ACTION_STALL)
        if not done:
            log("linux: Diktat-Rest zurückgelassen (Not-Aus)")
        return done

    def _linux_stall(self, secs):
        log("linux: Diktat-Rest hängt seit %.0fs; Thread-Stacks folgen. "
            "Not-Aus: kill -USR2 %d" % (secs, os.getpid()))
        faulthandler.dump_traceback()

    def _forget_lost_keys(self, pressed, gone, st):
        """Nach dem Verschwinden von Geräten (Deskriptoren in gone) deren
        gedrückte Tasten vergessen (Audit B35) und, wenn der Chord dadurch
        fehlt, so weitermachen, als wäre er losgelassen worden. Rückgabe
        (Zustand, jetzt einfügen?)."""
        if not any([pressed.drop(fd) for fd in gone]):
            return st, False
        group_a, group_b = CHORDS[self.cfg.chord]
        st, finish = keys_lost(st, pressed.chord(group_a, group_b))
        if finish:
            log("linux: Tastatur verschwand mit gehaltenem Chord -> Aufnahme endet")
        return st, finish

    def _install_panic_signal(self):
        """Not-Aus per Signal (SIGUSR2): beendet ein hängendes Diktat auch
        dann, wenn der Hotkey nicht mehr reagiert — unter macOS schickt der
        Menüpunkt „Diktat sofort beenden" genau dieses Signal, unter Linux
        `systemctl --user kill -s USR2 quasseld`."""
        if not hasattr(signal, "SIGUSR2"):
            return
        try:
            signal.signal(signal.SIGUSR2, self._on_panic_signal)
        except (OSError, ValueError):   # nicht im Haupt-Thread -> kein Handler
            log("daemon: SIGUSR2-Handler nicht installierbar (Not-Aus nur übers Menü)")
        self._install_raw_back_signal()

    def _on_panic_signal(self, *_):
        """Den Not-Aus in einem eigenen Thread ausführen: Python ruft den
        Handler auf dem Hauptthread zwischen zwei Bytecodes auf. Hält der
        gerade die Zustands- oder Recorder-Sperre, wartete panic_stop() hier
        auf seinen eigenen Thread. Der Handler wartet höchstens kurz auf das
        Ende, damit ein ungestörter Not-Aus wie bisher sofort fertig ist."""
        worker = threading.Thread(target=self.panic_stop, name="panic", daemon=True)
        worker.start()
        worker.join(PANIC_SIGNAL_WAIT)

    def _install_raw_back_signal(self):
        """„Rohtext zurück" per SIGHUP: das Kontrollzentrum (eigener Prozess
        unter Linux, Kindprozess-Eltern unter macOS) schickt das Signal; der
        Rohtext liegt nur hier im Speicher. Die Zwischenablage wird in einem
        eigenen Thread gesetzt, nicht im Signal-Handler."""
        if not hasattr(signal, "SIGHUP"):
            return
        try:
            signal.signal(signal.SIGHUP, lambda *_: threading.Thread(
                target=self.raw_back, name="raw-back", daemon=True).start())
        except (OSError, ValueError):
            log("daemon: SIGHUP-Handler nicht installierbar (Rohtext zurück aus)")

    def _mac_watchdog(self, listener, now=None):
        """Sicherheitslimit im Freihand-Modus (Pendant zu MAX_RECORD im
        Linux-Loop): zu lange Aufnahme hart beenden -> Text wird eingefügt.
        Zusätzlich: hängende Threads melden, notfalls den Not-Aus auslösen."""
        now = now if now is not None else time.monotonic()
        self._mac_check_stall(listener, now)
        if self.rec.active and now - self.rec.started > MAX_RECORD:
            if listener.force_finish():
                log("mac: MAX_RECORD erreicht -> Freihand-Aufnahme beendet")
            else:
                log("mac: MAX_RECORD erreicht, Hotkey antwortet nicht -> Not-Aus")
                self.panic_stop()
        elif self.autostop_due() and listener.force_finish():
            log("mac: Auto-Stopp nach Stille -> Freihand-Aufnahme beendet")

    def _mac_check_stall(self, listener, now):
        """Steht der Ereignis-Thread oder läuft eine Aktion zu lange, EINMALIG
        (entprellt, bis es wieder läuft) Zustand und die Stacks aller Threads
        in den Log schreiben — ohne das ist ein Hänger im Nachhinein nicht
        aufzuklären."""
        silent = now - listener.last_event_tick
        action = listener.current_action
        # Arbeitet der Ereignis-Thread nachweislich an einem synchronen Teil
        # (Gerät stoppen, osascript), ist er nicht still — er ist langsam. Dann
        # gilt die lange Frist, sonst schlüge der Selbstneustart weiter unten
        # mitten in ein zähes, aber normales Diktatende und würfe die eben
        # gelesenen Rohdaten weg.
        sync = getattr(listener, "current_sync", None)
        limit = ACTION_STALL if sync is not None else EVENT_STALL
        action_stalled = (action is not None
                          and now - listener.last_action_tick > ACTION_STALL)
        if not (silent > limit or action_stalled):
            self._stall_logged = False
            return
        if not self._stall_logged:
            self._stall_logged = True
            log("mac: Hotkey hängt — Ereignis-Thread %.1fs still, Aktion %r seit "
                "%.1fs, Maschine=%s, Aufnahme=%s; Thread-Stacks folgen"
                % (silent, action, now - listener.last_action_tick,
                   listener.machine.state, self.rec.active))
            faulthandler.dump_traceback()
        # Letzter Ausweg: der Ereignis-Thread steht schon beim Öffnen des
        # Audiogeräts fest (keine Aufnahme aktiv, also greift auch die
        # MAX_RECORD-Eskalation nicht). Von hier kommt der Prozess nicht mehr
        # zurück — die Aufsicht in mac_app.py startet ihn neu.
        dead_after = ACTION_STALL if sync is not None else DEAD_STALL
        if silent > dead_after and not self.rec.active:
            log("mac: Ereignis-Thread seit %.0fs tot (sync=%r) -> Daemon startet neu"
                % (silent, sync))
            notify(tr("audio_restart"))
            self._exit(RESTART_EXIT)

    def _record_expired(self, st, now):
        """Sicherheitslimit: eine Aufnahme läuft höchstens MAX_RECORD. Gilt
        auch im Halten-Modus (Audit B35): verschwindet die Tastatur, während
        der Chord gehalten wird, käme sonst nie ein Loslassen."""
        if st in ("toggle_armed", "toggle") and self.autostop_due():
            return True       # Freihand-Diktat nach Stille beenden ([vad] autostop)
        return (st in RECORDING_STATES and self.rec.active
                and now - self.rec.started > MAX_RECORD)

    def _run_linux(self):
        pressed = PressedKeys()
        st = "idle"                 # idle|hold|await2|toggle_armed|toggle|drain
        # Einfügen erst, wenn ALLE Modifier losgelassen sind — sonst käme beim
        # Ziel z.B. Strg+Meta+Shift+Einfg an statt Shift+Einfg.
        pending = False
        pending_since = 0.0
        t_chord = t_tap = 0.0
        fds = {}
        last_scan = 0.0

        scan_devices(fds)
        if not fds:
            log("FEHLER: Keine /dev/input-Geräte lesbar. Ist der Benutzer in der "
                "Gruppe 'input'? (Nach 'usermod -aG input' neu anmelden!)")
            sys.exit(1)
        state_set("idle")
        notify(tr("ready"), 2000)
        # Der lange Teil läuft auf einem Aktions-Thread, die Schleife wartet
        # darauf in kurzen Schritten: so kommt der Not-Aus (SIGUSR2) auch
        # dann durch, wenn Server oder Einfügen hängen (Audit D55).
        self._runner = dictation.ActionRunner(name="quassel-actions", log=log)
        self._install_panic_signal()
        self._sync_wakeword()

        while True:
            now = time.monotonic()
            if self._consume_linux_reset(pressed):
                st = "idle"
                pending = False
            if now - last_scan > RESCAN_EVERY:
                gone = []
                scan_devices(fds, on_close=gone.append)
                st, lost = self._forget_lost_keys(pressed, gone, st)
                if lost:
                    pending, pending_since = True, now
                # Config live nachladen (mtime-Check, billig): Hotkey-/
                # Timing-Änderungen aus den Einstellungen greifen sofort
                if self.cfg.reload():
                    i18n.set_language(None if self.cfg.ui_language == "auto"
                                      else self.cfg.ui_language)
                self._sync_wakeword()
                last_scan = now

            timeout = 0.05 if (pending or st == "await2") else 1.0
            try:
                rlist, _, _ = select.select(list(fds), [], [], timeout)
            except OSError:
                gone = []
                scan_devices(fds, on_close=gone.append)
                st, lost = self._forget_lost_keys(pressed, gone, st)
                if lost:
                    pending, pending_since = True, now
                continue

            now = time.monotonic()
            group_a, group_b = CHORDS[self.cfg.chord]
            mods = group_a | group_b

            # einzelner kurzer Tipp ohne zweiten -> verwerfen
            if st == "await2" and now - t_tap > self.cfg.double_window:
                self.cancel_recording("canceled_tap")
                st = "idle"
            # Sicherheitslimit, auch im Halten-Modus
            if self._record_expired(st, now):
                log("linux: Aufnahmelimit oder Auto-Stopp -> Aufnahme wird beendet")
                st = "idle"
                pending, pending_since = True, now

            for fd in rlist:
                try:
                    data = os.read(fd, EVENT_SIZE * 64)
                except OSError:
                    fds.pop(fd, None)
                    os.close(fd)
                    st, lost = self._forget_lost_keys(pressed, [fd], st)
                    if lost:
                        pending, pending_since = True, now
                    continue
                for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack_from(EVENT_FORMAT, data, off)
                    if etype != EV_KEY or value == 2:
                        continue
                    now = time.monotonic()

                    if code in mods:
                        before = pressed.chord(group_a, group_b)
                        if value == KEY_PRESS:
                            pressed.press(fd, code)
                        else:
                            pressed.release(fd, code)
                        chord = pressed.chord(group_a, group_b)

                        if chord and not before:        # Chord komplett gedrückt
                            if st == "idle" and not pending:
                                if self.start_recording():
                                    st = "hold"
                                    t_chord = now
                            elif st == "await2":
                                st = "toggle_armed"     # Doppeltipp erkannt
                                self.enable_streaming()  # Freihand -> ggf. Streaming
                            elif st == "toggle":
                                st = "drain"
                                pending, pending_since = True, now
                        elif before and not chord:      # Chord gelöst
                            if st == "hold":
                                if now - t_chord >= self.cfg.hold_min:
                                    st = "idle"
                                    pending, pending_since = True, now
                                else:
                                    st = "await2"       # evtl. 1. Tipp eines Doppeltipps
                                    t_tap = now
                            elif st == "toggle_armed":
                                st = "toggle"
                            elif st == "drain":
                                st = "idle"
                    else:
                        # andere Taste während gehaltenem Chord = normales
                        # Tastenkürzel (z.B. Strg+Meta+Pfeil) -> abbrechen
                        if value == KEY_PRESS and st in ("hold", "toggle_armed"):
                            self.cancel_recording("canceled_key")
                            st = "drain" if pressed.chord(group_a, group_b) else "idle"

            # Ausstehendes Einfügen, sobald alle Modifier losgelassen sind
            # (Fallback nach 2 s, falls ein Release-Event verloren ging)
            if pending and (not pressed or time.monotonic() - pending_since > 2.0):
                pending = False
                pressed.clear()
                self._linux_finish()


class PressedKeys:
    """Gedrückte Modifier je Eingabegerät (Audit B35). Ein Gerät, das
    verschwindet (Bluetooth-Tastatur schläft ein, Hub), sendet kein Loslassen
    mehr; ohne Zuordnung blieben seine Tasten für immer gedrückt, und der
    nächste Chord würde nicht mehr erkannt."""

    def __init__(self):
        self._by_fd = {}

    def press(self, fd, code):
        self._by_fd.setdefault(fd, set()).add(code)

    def release(self, fd, code):
        keys = self._by_fd.get(fd)
        if keys is not None:
            keys.discard(code)
            if not keys:
                del self._by_fd[fd]

    def drop(self, fd):
        """Tasten eines verschwundenen Geräts vergessen. True = es war eine
        gedrückt."""
        return bool(self._by_fd.pop(fd, None))

    def codes(self):
        out = set()
        for keys in self._by_fd.values():
            out |= keys
        return out

    def chord(self, group_a, group_b):
        codes = self.codes()
        return bool(codes & group_a) and bool(codes & group_b)

    def clear(self):
        self._by_fd.clear()

    def __bool__(self):
        return any(self._by_fd.values())


def keys_lost(st, chord_down):
    """Übergang, wenn Tasten durch ein verschwundenes Gerät wegfallen: fehlt
    der Chord jetzt, gilt er als losgelassen. Rückgabe (Zustand, einfügen?)."""
    if chord_down:
        return st, False
    if st == "hold":
        return "idle", True          # wie Loslassen nach dem Halten
    if st == "toggle_armed":
        return "toggle", False       # Freihand läuft weiter
    if st == "drain":
        return "idle", False
    return st, False


def scan_devices(fds, on_close=None):
    """Alle lesbaren /dev/input/event* öffnen (neue Geräte nachladen).
    on_close(fd) erfährt jedes geschlossene Gerät, BEVOR seine Nummer an ein
    neues gehen kann."""
    present = set()
    try:
        entries = os.listdir("/dev/input")
    except OSError:
        return
    for path in entries:
        if not path.startswith("event"):
            continue
        full = "/dev/input/" + path
        present.add(full)
        if full in fds.values():
            continue
        try:
            fd = os.open(full, os.O_RDONLY | os.O_NONBLOCK)
            fds[fd] = full
        except OSError:
            pass
    for fd, path in list(fds.items()):
        if path not in present:
            if on_close is not None:
                on_close(fd)
            os.close(fd)
            del fds[fd]


def install_diagnostics():
    """Stacks aller Threads auf Zuruf: `kill -USR1 <pid>` schreibt sie in den
    Log. Nur auf macOS registriert — unter Linux beendet SIGUSR1 den Prozess
    per Voreinstellung, und dieses Verhalten bleibt dort unangetastet."""
    faulthandler.enable()
    if sys.platform != "darwin" or not hasattr(signal, "SIGUSR1"):
        return
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (OSError, ValueError, RuntimeError):
        log("mac: faulthandler/SIGUSR1 nicht registrierbar")


def main():
    install_diagnostics()
    _install_server_recovery()
    log("quassel-daemon %s gestartet (%s, pid=%d)"
        % (__version__, time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    from .state import mark_daemon_running
    mark_daemon_running()
    try:
        Daemon().run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
