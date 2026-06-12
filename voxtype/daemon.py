"""VoxType-Daemon: Modifier-Chord-Erkennung (Standard Strg+Meta) über evdev.

Modi:
  * Halten:     Chord halten, sprechen, loslassen -> Text wird eingefügt
  * Doppeltipp: Chord 2× kurz tippen -> freihändig sprechen;
                1× drücken -> Text wird eingefügt

Während der Aufnahme entstehen alle ~2 s Teiltranskripte (letzte 15 s Audio)
für die Live-Vorschau in der Pille (state.json).
"""
import os
import select
import struct
import sys
import threading
import time

from . import config, i18n, textproc, whisperclient
from .streaming import StreamTyper
from .audio import RATE, SAMPLE_BYTES, Recorder, wav_from_raw
from .config import CHORDS
from .i18n import tr
from .platform_linux import (notify, paste, send_backspaces, type_chunk,
                             streaming_begin, streaming_restore)
from .state import PARTWAV, WAV, state_set

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 1
KEY_PRESS = 1

MAX_RECORD = 300       # s: Sicherheitslimit im Freihand-Modus
RESCAN_EVERY = 5       # s: /dev/input auf neue Tastaturen prüfen
PARTIAL_EVERY = 2.0    # s: Abstand der Live-Vorschau-Transkripte
PARTIAL_WINDOW = 15    # s: Vorschau nutzt nur die letzten N Sekunden Audio


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class PartialLoop(threading.Thread):
    """Erzeugt während der Aufnahme Teiltranskripte für Live-Vorschau und
    (im Freihand-Modus mit aktivem Streaming) für das Live-Tippen.

    streamer: optionaler StreamTyper. show_preview: ob die Pillen-Blase den
    Vorschautext zeigt (im Streaming nur den noch nicht getippten Rest)."""

    def __init__(self, rec, cfg, owner):
        super().__init__(daemon=True)
        self.rec, self.cfg = rec, cfg
        self.owner = owner          # Daemon — streamer wird ggf. mitten im Lauf gesetzt
        self.stop_event = threading.Event()

    def run(self):
        whisperclient.ensure_server()   # Modell vorladen -> finale Transkription flott
        while not self.stop_event.wait(PARTIAL_EVERY):
            if not self.rec.active:
                return
            data = self.rec.raw_bytes()
            if len(data) < RATE * SAMPLE_BYTES // 2:   # < 0,5 s
                continue
            data = data[-(RATE * SAMPLE_BYTES * PARTIAL_WINDOW):]
            try:
                wav_from_raw(data, PARTWAV)
            except OSError:
                continue
            raw = whisperclient.transcribe(PARTWAV, self.cfg, timeout=20)
            if raw is None or self.stop_event.is_set() or not self.rec.active:
                continue
            kind, text = textproc.postprocess(raw, self.cfg)
            if kind != "text":
                continue
            streamer = self.owner.streamer
            show = self.cfg.pill_preview
            if streamer is not None:
                streamer.update(text)
                # Blase zeigt nur den noch nicht getippten Rest (oder nichts)
                state_set("recording",
                          text[len(streamer.typed):].strip() if show else "")
            else:
                state_set("recording", text if show else "")

    def stop(self):
        self.stop_event.set()


class Daemon:
    def __init__(self):
        self.cfg = config.Cfg()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        self.rec = Recorder()
        self.partial = None
        self.last_paste_len = 0
        self.streamer = None        # aktiv nur im Freihand-Modus mit Streaming
        self._clip_backup = None

    # ------------------------------------------------------------- Aufnahme
    def start_recording(self):
        self.cfg.reload()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        if not self.rec.start(self.cfg.mic):
            notify("Fehler: pw-record/parecord fehlt")
            return False
        self.streamer = None
        self.partial = PartialLoop(self.rec, self.cfg, self)
        self.partial.start()
        state_set("recording")
        return True

    def enable_streaming(self):
        """Beim Wechsel in den Freihand-Modus: Streaming starten, falls
        eingeschaltet. (Im Halten-Modus technisch unmöglich -> nie hier.)"""
        if self.streamer is not None or not self.cfg.streaming:
            return
        self._clip_backup = streaming_begin()

        def typ(chunk):
            type_chunk(chunk)

        def dele(n):
            send_backspaces(n)
        self.streamer = StreamTyper(self.cfg.streaming_mode, typ, dele)

    def cancel_recording(self, reason_key):
        if self.partial:
            self.partial.stop()
            self.partial = None
        if self.streamer is not None:
            streaming_restore(self._clip_backup)
            self.streamer = None
        self.rec.stop()
        state_set("idle")
        notify("✖ " + tr(reason_key))

    def finish(self):
        """Aufnahme beenden, transkribieren, einfügen bzw. Kommando ausführen."""
        if self.partial:
            self.partial.stop()
            self.partial = None
        self.rec.stop()
        data = self.rec.raw_bytes()
        if len(data) < 8000:  # < ~0,25 s Audio
            state_set("idle")
            notify(tr("too_short"))
            return
        state_set("transcribing")
        if not whisperclient.ensure_server():
            state_set("error", tr("no_server"))
            return
        wav_from_raw(data, WAV)
        raw_text = whisperclient.transcribe(WAV, self.cfg)
        if raw_text is None:
            state_set("error", tr("no_server"))
            return
        kind, value = textproc.postprocess(raw_text, self.cfg)
        if kind is None:
            if self.streamer is not None:
                streaming_restore(self._clip_backup)
                self.streamer = None
            state_set("error", tr("nothing"))
            return
        if kind == "command":
            # Im Streaming wurde live getippt -> getippte Länge zurücknehmen
            undo = len(self.streamer.typed) if self.streamer is not None else self.last_paste_len
            if self.streamer is not None:
                streaming_restore(self._clip_backup)
                self.streamer = None
            if undo > 0:
                send_backspaces(undo)
                self.last_paste_len = 0
                state_set("done", tr("deleted"))
            else:
                state_set("error", tr("nothing"))
            return
        if self.streamer is not None:
            # Streaming: Zielfenster exakt auf den Endtext bringen (inkl. der
            # zurückgehaltenen Zeilenumbrüche), dann Zwischenablage zurück
            typed = self.streamer.finish(value)
            self.last_paste_len = len(typed)
            streaming_restore(self._clip_backup)
            self.streamer = None
        else:
            paste(value)
            self.last_paste_len = len(value)
        state_set("done", value)
        if self.cfg.history_enabled:
            try:
                config.history_append(value)
            except OSError:
                pass

    # ------------------------------------------------------------- Hauptloop
    def run(self):
        pressed = set()
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

        while True:
            now = time.monotonic()
            if now - last_scan > RESCAN_EVERY:
                scan_devices(fds)
                # Config live nachladen (mtime-Check, billig): Hotkey-/
                # Timing-Änderungen aus den Einstellungen greifen sofort
                if self.cfg.reload():
                    i18n.set_language(None if self.cfg.ui_language == "auto"
                                      else self.cfg.ui_language)
                last_scan = now

            timeout = 0.05 if (pending or st == "await2") else 1.0
            try:
                rlist, _, _ = select.select(list(fds), [], [], timeout)
            except OSError:
                scan_devices(fds)
                continue

            now = time.monotonic()
            group_a, group_b = CHORDS[self.cfg.chord]
            mods = group_a | group_b

            # einzelner kurzer Tipp ohne zweiten -> verwerfen
            if st == "await2" and now - t_tap > self.cfg.double_window:
                self.cancel_recording("canceled_tap")
                st = "idle"
            # Sicherheitslimit im Freihand-Modus
            if st in ("toggle", "toggle_armed") and self.rec.active \
                    and now - self.rec.started > MAX_RECORD:
                st = "idle"
                pending, pending_since = True, now

            for fd in rlist:
                try:
                    data = os.read(fd, EVENT_SIZE * 64)
                except OSError:
                    fds.pop(fd, None)
                    os.close(fd)
                    continue
                for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack_from(EVENT_FORMAT, data, off)
                    if etype != EV_KEY or value == 2:
                        continue
                    now = time.monotonic()

                    if code in mods:
                        before = bool(pressed & group_a) and bool(pressed & group_b)
                        if value == KEY_PRESS:
                            pressed.add(code)
                        else:
                            pressed.discard(code)
                        chord = bool(pressed & group_a) and bool(pressed & group_b)

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
                            st = "drain" if (pressed & group_a and pressed & group_b) else "idle"

            # Ausstehendes Einfügen, sobald alle Modifier losgelassen sind
            # (Fallback nach 2 s, falls ein Release-Event verloren ging)
            if pending and (not pressed or time.monotonic() - pending_since > 2.0):
                pending = False
                pressed.clear()
                self.finish()


def scan_devices(fds):
    """Alle lesbaren /dev/input/event* öffnen (neue Geräte nachladen)."""
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
            os.close(fd)
            del fds[fd]


def main():
    try:
        Daemon().run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
