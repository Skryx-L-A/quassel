"""Quassel-Daemon: Modifier-Chord-Erkennung (Standard Strg+Meta) über evdev.

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

from . import (aimodes, beep, config, i18n, learn, progmode, stats, textproc,
               textreplace, vad, wakeword, whisperclient)
from .mediacontrol import AudioDucker
from .streaming import StreamTyper
from .audio import RATE, SAMPLE_BYTES, Recorder, wav_from_raw
from .config import CHORDS
from .i18n import tr
from .platform_linux import (mic_is_bluetooth, notify, paste, send_backspaces,
                             send_enter, type_chunk, streaming_begin, streaming_restore)
from .state import PARTWAV, WAV, state_set

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 1
KEY_PRESS = 1

MAX_RECORD = 300       # s: Sicherheitslimit im Freihand-Modus
RESCAN_EVERY = 5       # s: /dev/input auf neue Tastaturen prüfen
PARTIAL_EVERY = 2.0    # s: Abstand der Live-Vorschau-Transkripte
PARTIAL_WINDOW = 15    # s: Vorschau nutzt nur die letzten N Sekunden Audio

# Eigene Roh-/WAV-Dateien für den Wake-Listener (getrennt vom Tastatur-Pfad)
WAKE_RAW = os.path.join(os.path.dirname(WAV), "wake.raw")
WAKE_WAV = os.path.join(os.path.dirname(WAV), "wake.wav")

# Anfang verwerfen (Mikro-Einschwingen / Start-Ton) und Ende puffern (nicht
# abschneiden). Bei Bluetooth großzügiger — Profilwechsel frisst Anfang/Ende.
LEAD_TRIM_MS, LEAD_TRIM_BT_MS = 80, 400
TAIL_PAD_MS, TAIL_PAD_BT_MS = 200, 350


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
            # Ohne Streaming UND ohne Vorschaublase braucht niemand das
            # Teiltranskript — die teure Transkription dann überspringen
            # (spart auf schwacher CPU viel Last, die sonst das Finale bremst).
            if self.owner.streamer is None and not self.cfg.pill_preview:
                continue
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
        self.ducker = AudioDucker()  # Musik pausieren / Ton stummschalten beim Diktieren
        self.wake = None             # WakeListener-Thread (nur wenn Wake-Word an)
        self._bt = False             # ist die aktive Aufnahmequelle Bluetooth?

    # ------------------------------------------------------------- Aufnahme
    def start_recording(self):
        self.cfg.reload()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        if not self.rec.start(self.cfg.mic):
            notify("Fehler: pw-record/parecord fehlt")
            return False
        self._bt = mic_is_bluetooth(self.cfg.mic)
        self.ducker.apply(self.cfg.mute_mode)
        if self.cfg.beep:
            beep.start()                 # aufsteigender Ton: jetzt sprechen
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
        if self.cfg.beep:
            beep.stop()
        self.ducker.restore()
        state_set("idle")
        notify("✖ " + tr(reason_key))

    def finish(self):
        """Aufnahme beenden, transkribieren, einfügen bzw. Kommando ausführen."""
        if self.partial:
            self.partial.stop()
            self.partial = None
        # Nachlauf: kurz weiter aufnehmen, damit das letzte Wort nicht abschneidet
        # (Bluetooth braucht mehr). Dann stoppen + Stopp-Ton.
        time.sleep((TAIL_PAD_BT_MS if self._bt else TAIL_PAD_MS) / 1000.0)
        self.rec.stop()
        if self.cfg.beep:
            beep.stop()
        self.ducker.restore()
        data = self.rec.raw_bytes()
        # Vorlauf verwerfen (Mikro-Einschwingen / Start-Ton / BT-Profilwechsel),
        # aber nur wenn danach genug Audio übrig bleibt.
        trim = int(RATE * SAMPLE_BYTES * (LEAD_TRIM_BT_MS if self._bt else LEAD_TRIM_MS) / 1000)
        trim -= trim % SAMPLE_BYTES
        if len(data) > trim + 8000:
            data = data[trim:]
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
            action = value
            # Im Streaming wurde das Kommando ("scratch that" / "press enter")
            # selbst live getippt -> diese Länge zurücknehmen.
            live_typed = len(self.streamer.typed) if self.streamer is not None else 0
            if self.streamer is not None:
                streaming_restore(self._clip_backup)
                self.streamer = None
            if action == "enter":
                if live_typed:
                    send_backspaces(live_typed)
                send_enter()
                self.last_paste_len = 0
                state_set("done", tr("pressed_enter"))
                return
            # action == "undo": im Streaming das live Getippte, sonst letztes Diktat
            undo = live_typed if live_typed else self.last_paste_len
            if undo > 0:
                send_backspaces(undo)
                self.last_paste_len = 0
                state_set("done", tr("deleted"))
            else:
                state_set("error", tr("nothing"))
            return
        mech = self._refine_mechanical(value)
        if self.streamer is not None:
            # Streaming: KI auf den Endtext, dann Zielfenster exakt darauf bringen.
            final = self._ai_refine(mech) if self.cfg.ai_enabled else mech
            typed = self.streamer.finish(final)
            self.last_paste_len = len(typed)
            streaming_restore(self._clip_backup)
            self.streamer = None
            value = final
        elif self.cfg.ai_enabled:
            # Lokale KI braucht Sekunden. ERST den Rohtext einfügen (solange der
            # Fokus sicher im Zielfeld ist), DANN durch die KI-Fassung ersetzen —
            # sonst landet bei langsamen Modellen nichts mehr im Fenster.
            paste(mech)
            self.last_paste_len = len(mech)
            state_set("transcribing")
            final = self._ai_refine(mech)
            if final != mech:
                send_backspaces(len(mech))
                paste(final)
                self.last_paste_len = len(final)
                log("ai: replaced %d -> %d chars" % (len(mech), len(final)))
            value = final
        else:
            paste(mech)
            self.last_paste_len = len(mech)
            value = mech
        state_set("done", value)
        secs = len(data) / (RATE * SAMPLE_BYTES)
        self._after_insert(value, secs)

    def _refine_mechanical(self, text):
        """Schnelle lokale Umformungen ohne Netz/KI: Programmier-Diktat + Textersetzungen."""
        if self.cfg.programmer_mode:
            text = progmode.apply(text)
        if self.cfg.text_replace:
            rules = config.replacement_rules()
            if rules:
                text = textreplace.apply_rules(text, rules)
        return text

    def _refine(self, text):
        """Mechanische Umformungen + (optional) lokale KI — synchron (Wake-Pfad)."""
        text = self._refine_mechanical(text)
        if self.cfg.ai_enabled:
            text = self._ai_refine(text)
        return text

    def _ai_refine(self, text):
        """Lokale KI-Nachbearbeitung (Ollama): Sprach-Modus ('als E-Mail', …) hat
        Vorrang, sonst optional ein Auto-Modus auf jedes Diktat. Schlägt etwas fehl
        oder läuft Ollama nicht, bleibt der Rohtext (KI darf nie Worte verlieren)."""
        self.cfg.ai_modes_text = config.ai_modes_text()
        mode, remaining = None, text
        if self.cfg.ai_voice_modes:
            m, rest = aimodes.detect_voice_mode(text)
            if m is None:
                m, rest = self._detect_custom_voice(text)
            if m:
                mode, remaining = m, rest
        if mode is None and self.cfg.ai_post_process:
            mode = self.cfg.ai_post_mode
        if mode is None or not remaining.strip():
            return text
        return aimodes.transform(remaining, mode, self.cfg)

    def _detect_custom_voice(self, text):
        """Eigenen Modusnamen als Eröffnungsphrase erkennen ('tweet: …')."""
        custom = aimodes.parse_custom(getattr(self.cfg, "ai_modes_text", "") or "")
        low = text.lower().strip()
        for name in sorted(custom, key=len, reverse=True):
            if low.startswith(name):
                rest = text.strip()[len(name):].lstrip(" ,:").strip()
                return name, rest
        return None, text

    def _after_insert(self, value, seconds=0.0):
        """Nach dem Einfügen: Verlauf, Statistik, Wörterbuch-Lernen."""
        if self.cfg.history_enabled:
            try:
                config.history_append(value)
            except OSError:
                pass
        if self.cfg.stats_enabled:
            try:
                stats.record(value, seconds=seconds)
            except Exception:    # noqa: BLE001 — Statistik darf nie stören
                pass
        if self.cfg.auto_learn:
            try:
                merged, added = learn.learn(value, config.dictionary_words())
                if added:
                    config.dictionary_save("\n".join(merged))
            except Exception:    # noqa: BLE001 — Lernen darf nie stören
                pass

    # ----------------------------------------------------- Wake-Word (opt-in)
    def _sync_wakeword(self):
        """Listener nach Konfig starten/stoppen (idempotent)."""
        if self.cfg.wakeword_enabled and self.wake is None:
            self.wake = wakeword.WakeListener(
                self.cfg, self._wake_record_utterance, self._wake_transcribe,
                self._wake_insert, is_busy=lambda: self.rec.active)
            self.wake.start()
        elif not self.cfg.wakeword_enabled and self.wake is not None:
            self.wake.stop()
            self.wake = None

    def _wake_record_utterance(self):
        """Eine Äußerung über VAD aufnehmen (für den Wake-Listener). Eigener
        Recorder + eigene Rohdatei, damit der Tastatur-Pfad und die Pille
        ungestört bleiben. Schwelle wird am Grundrauschen kalibriert."""
        rec = Recorder(raw_path=WAKE_RAW)
        if not rec.start(self.cfg.mic):
            return None
        prev = 0
        det = None
        peak = 0.0
        base_samples = []
        deadline = time.monotonic() + 12
        try:
            while time.monotonic() < deadline and self.wake is not None:
                time.sleep(0.1)
                data = rec.raw_bytes()
                new = data[prev:]
                prev = len(data)
                if not new:
                    continue
                lvl = vad.frame_rms(new)
                peak = max(peak, lvl)
                if det is None:
                    # erste ~0.4 s: Grundrauschen messen, dann Schwelle setzen
                    base_samples.append(lvl)
                    if len(base_samples) >= 4:
                        noise = sorted(base_samples)[len(base_samples) // 2]
                        thr = max(220.0, noise * 2.2)
                        det = vad.SilenceDetector(silence_rms=thr,
                                                  min_speech_sec=0.25, hang_sec=1.0)
                        det.feed(new)   # Kalibrier-Frames nur als Rauschwert genutzt
                else:
                    det.feed(new)
                    if det.stopped:
                        break
        finally:
            rec.stop()
        data = rec.raw_bytes()
        started = det.speech_started if det is not None else False
        log("wake: rec %d bytes, peak_rms=%.0f, speech=%s" % (len(data), peak, started))
        if not started or len(data) < 8000:
            return None
        return data

    def _wake_transcribe(self, pcm):
        if not pcm or not whisperclient.ensure_server():
            return None
        try:
            wav_from_raw(pcm, WAKE_WAV)
        except OSError:
            return None
        # Wake-Phrase als Bias mitgeben, damit Whisper das Kunstwort eher trifft.
        text = whisperclient.transcribe(WAKE_WAV, self.cfg, timeout=30,
                                        prompt_extra=self.cfg.wakeword_phrase)
        log("wake: heard %r (phrase=%r)" % ((text or "").strip(), self.cfg.wakeword_phrase))
        return text

    def _wake_insert(self, raw_text):
        kind, value = textproc.postprocess(raw_text, self.cfg)
        if kind != "text" or not value.strip():
            return
        value = self._refine(value)
        paste(value)
        self.last_paste_len = len(value)
        state_set("done", value)
        self._after_insert(value)

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
        self._sync_wakeword()

        while True:
            now = time.monotonic()
            if now - last_scan > RESCAN_EVERY:
                scan_devices(fds)
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
