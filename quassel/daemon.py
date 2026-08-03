"""Quassel-Daemon: Modifier-Chord-Erkennung (Standard Strg+Meta) über evdev.

Modi:
  * Halten:     Chord halten, sprechen, loslassen -> Text wird eingefügt
  * Doppeltipp: Chord 2× kurz tippen -> freihändig sprechen;
                1× drücken -> Text wird eingefügt

Während der Aufnahme entstehen alle ~2 s Teiltranskripte (letzte 15 s Audio)
für die Live-Vorschau in der Pille (state.json).
"""
import faulthandler
import glob
import os
import select
import signal
import struct
import sys
import threading
import time

from . import __version__
from . import (aimodes, beep, config, i18n, learn, progmode, stats, textproc,
               textreplace, vad, wakeword, whisperclient)
from .mediacontrol import AudioDucker
from .streaming import StreamTyper
from .audio import RATE, SAMPLE_BYTES, Recorder, mac_backend, wav_from_raw
from .config import CHORDS
from .i18n import tr
from .platform import (mic_is_bluetooth, notify, paste, send_backspaces,
                       send_enter, type_chunk, streaming_begin, streaming_restore)
from .state import PARTWAV, RUNDIR, WAV, state_set

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 1
KEY_PRESS = 1

MAX_RECORD = 300       # s: Sicherheitslimit im Freihand-Modus
RESCAN_EVERY = 5       # s: /dev/input auf neue Tastaturen prüfen
# Am Diktat-Ende wartet niemand zwei Minuten auf einen Server, der ohnehin
# schon lief. Beim Kaltstart lädt er dagegen erst das Modell — dort wird lange
# gewartet, statt ein fertiges Diktat wegzuwerfen.
SERVER_WAIT_FINISH = 30
SERVER_WAIT_COLD = 600
# Gerettete Aufnahmen: Server tot oder Transkription gescheitert.
RESCUE_NAME = "rescued-%Y%m%d-%H%M%S"       # via time.strftime, ohne Endung
RESCUE_GLOB = "rescued-*.wav"
RESCUE_KEEP = 5
PANIC_EXIT_WAIT = 5.0  # s: so lange wartet der Not-Aus auf das Ende einer Aktion
SLOW_STOP = 0.5        # s: ab hier ist das Stoppen der Aufnahme meldenswert
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
PARTIAL_EVERY = 2.0    # s: Mindestabstand der Live-Vorschau-Transkripte
PARTIAL_WINDOW = 15    # s: Vorschau nutzt nur die letzten N Sekunden Audio
# Obergrenze für den adaptiven Abstand (PartialLoop.run): ein einzelner
# langsamer Durchlauf (Server-Kaltstart, kurzer Systemhänger) soll die
# Vorschau nicht minutenlang verstummen lassen. 10 s = Fünffaches des
# Mindestabstands -- genug Puffer für eine kurze Verlangsamung, aber die
# Vorschau meldet sich spätestens nach 10 s wieder.
PARTIAL_MAX_WAIT = 10.0

# Eigene Roh-/WAV-Dateien für den Wake-Listener (getrennt vom Tastatur-Pfad)
WAKE_RAW = os.path.join(os.path.dirname(WAV), "wake.raw")
WAKE_WAV = os.path.join(os.path.dirname(WAV), "wake.wav")

# Vorlauf verwerfen + Ende puffern. WICHTIG: läuft VAD (serverseitig), entfernt
# es Stille/BT-Ramp am Anfang selbst — dann NUR unseren eigenen Start-Ton
# wegschneiden (sonst doppelt geschnitten -> erstes Wort weg). Ohne VAD den
# BT-Profilwechsel-Müll am Anfang großzügig trimmen.
LEAD_TRIM_MS = 160            # nur der Start-Ton (mit VAD)
LEAD_TRIM_NOVAD_BT_MS = 400   # ohne VAD: BT-Ramp am Anfang
TAIL_PAD_MS, TAIL_PAD_BT_MS = 250, 450


def log(msg):
    # Zeitstempel: im Log der App (~/Library/Logs/Quassel/daemon.log) ließe
    # sich sonst weder ein Neustart noch die Dauer eines Hängers ablesen.
    print("%s %s" % (time.strftime("%H:%M:%S"), msg), file=sys.stderr, flush=True)


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
        # Adaptiver Abstand statt fest PARTIAL_EVERY: der Server bearbeitet
        # Teil- und Finaltranskript NACHEINANDER, und ein Teiltranskript
        # kostet fast so viel wie das Finale (whisper.cpp füllt jede Eingabe
        # ohnehin auf ein volles 30-s-Fenster auf). Bei festem Abstand wäre
        # der Server während des ganzen Diktats > 80% ausgelastet, gemessen
        # im CPU-Sparlauf. delay wird nach jedem Durchlauf auf dessen eigene
        # Dauer angehoben (Untergrenze PARTIAL_EVERY, Obergrenze
        # PARTIAL_MAX_WAIT) -> Auslastung bleibt strukturell < 50%.
        delay = PARTIAL_EVERY
        while not self.stop_event.wait(delay):
            iter_start = time.monotonic()
            try:
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
                # Erneut prüfen, unmittelbar VOR der teuren Transkription: das
                # Diktat kann zwischen Schleifenstart und hier beendet worden
                # sein. Ohne diese zweite Prüfung würde ein Teiltranskript
                # starten, obwohl das Finale schon ansteht -- und sich hinter
                # ihm in die Warteschlange des Servers stellen.
                if self.stop_event.is_set() or not self.rec.active:
                    return
                raw = whisperclient.transcribe(PARTWAV, self.cfg, timeout=20)
                # Ehrlich bleiben: die HTTP-Anfrage selbst lässt sich
                # clientseitig nicht abbrechen, sie läuft am Server so oder so
                # durch, und das Finale wartet trotzdem dahinter. Hier wird
                # nur noch das ERGEBNIS verworfen, kein Zeitgewinn erzielt.
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
            finally:
                delay = max(PARTIAL_EVERY,
                            min(time.monotonic() - iter_start, PARTIAL_MAX_WAIT))

    def stop(self):
        self.stop_event.set()


class Daemon:
    # Entprellung der Watchdog-Warnung: eine Meldung je Hänger, nicht alle 5 s.
    _stall_logged = False
    _listener = None            # MacHotkeyListener, nur im mac-Loop gesetzt
    # Not-Aus-Ereignis des gerade laufenden Diktats (je Diktat eines, siehe
    # _finish_capture) und Name des laufenden synchronen Teils.
    _panic_flag = None
    _sync_action = None
    # Prozessende ohne Aufräumen: ein reguläres sys.exit liefe beim Abbau der
    # Audio-Bibliothek erneut in den verklemmten CoreAudio-Mutex. Als Attribut,
    # damit Tests den Aufruf abfangen können, statt sich selbst zu beenden.
    _exit = os._exit

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
        self._vad = False            # läuft serverseitig VAD? (dann Vorlauf nicht doppelt trimmen)

    # ------------------------------------------------------------- Aufnahme
    def start_recording(self):
        self.cfg.reload()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        # Starten und Beenden laufen auf DEMSELBEN Thread (dem Ereignis-Thread
        # des Hotkeys, im Linux-Loop der Hauptschleife): die Reihenfolge steht
        # damit strukturell fest, kein Schloss muss sie herstellen.
        if not self.rec.start(self.cfg.mic):
            if sys.platform == "darwin":
                # sounddevice-Pfad: es fehlt kein Programm, sondern ein
                # nutzbares Eingabegerät (oder die Mikrofon-Freigabe).
                missing = ("ffmpeg" if mac_backend() == "ffmpeg"
                           else "Mikrofonzugriff")
            else:
                missing = "pw-record/parecord"
            notify("Fehler: %s fehlt" % missing)
            return False
        self._bt = mic_is_bluetooth(self.cfg.mic)
        self._vad = bool(config.read_serverenv().get("VAD_MODEL"))
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
        # Wie finish(): läuft synchron auf dem Ereignis-Thread und zählt
        # solange als laufendes Diktat (siehe _busy).
        self._sync_action = "cancel"
        try:
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
        finally:
            self._sync_action = None
        # Ein Stream, der beim Abbruch aufgegeben werden musste, vergiftet den
        # Prozess genauso wie einer am Diktat-Ende.
        self._restart_if_audio_poisoned()

    def panic_stop(self, *_args):
        """Not-Aus: laufende Aufnahme sofort beenden, ohne zu transkribieren.

        Aus JEDEM Thread aufrufbar, auch aus dem SIGUSR2-Handler. Jeder Schritt
        hier hat eine harte Frist (Recorder-Stopp, osascript), damit der Not-Aus
        nicht genau dort hängen bleibt, wo das Diktat schon hängt. Läuft weder
        eine Aufnahme noch eine Aktion, gibt es nichts zu beenden — dann sagt
        die Methode das und tut sonst nichts."""
        if not self._busy():
            log("panic: Not-Aus ohne laufendes Diktat")
            notify(tr("nothing_running"))
            return
        # Merker des LAUFENDEN Diktats: wer „sofort beenden" drückt, will von
        # diesem Diktat keinen Text mehr. Ein später begonnenes Diktat hat sein
        # eigenes Ereignis und bleibt davon unberührt.
        if self._panic_flag is not None:
            self._panic_flag.set()
        log("panic: Not-Aus -> laufendes Diktat wird abgebrochen")
        if self.partial:
            self.partial.stop()
            self.partial = None
        if self.streamer is not None:
            streaming_restore(self._clip_backup)
            self.streamer = None
        try:
            self.rec.stop()
        except Exception as exc:   # noqa: BLE001 — der Not-Aus darf an nichts scheitern
            log("panic: rec.stop() fehlgeschlagen: %r" % exc)
        if self.cfg.beep:
            beep.stop()
        self.ducker.restore()
        state_set("idle")
        notify(tr("panic_stopped"))
        # Die Maschine steht nach einem Not-Aus womöglich noch auf "toggle" —
        # der nächste Chord-Druck liefe sonst in ein finish() ohne Aufnahme.
        if self._listener is not None:
            self._listener.force_reset()
        self._restart_if_audio_poisoned(after_panic=True)

    def _busy(self):
        """Läuft gerade ein Diktat? Drei Zustände zählen dazu: die Aufnahme
        selbst, der synchrone Teil des Beendens (zwischen rec.stop() und der
        Übergabe an den Aktions-Thread — dort ist die Aufnahme schon aus und
        die Aktion noch nicht gestartet) und der lange Rest."""
        if self.rec.active or self._sync_action is not None:
            return True
        return (self._listener is not None
                and self._listener.current_action is not None)

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

    def finish(self):
        """Aufnahme beenden — der SYNCHRONE Teil, er läuft auf dem Thread, der
        auch die Aufnahmen startet (Hotkey-Ereignis-Thread bzw. der Linux-Loop).

        Nachlauf, Recorder stoppen und Rohdaten lesen bleiben damit strukturell
        vor dem Start des nächsten Diktats — kein Schloss kann diese Reihenfolge
        herstellen, ein gemeinsamer Thread schon. Alles Langsame (Server,
        Transkription, Nachbearbeitung, Einfügen) kommt als Callable zurück und
        läuft auf dem Aktions-Thread; None heißt, es gibt nichts mehr zu tun."""
        rest = None
        self._sync_action = "finish"
        try:
            rest = self._finish_capture()
        finally:
            self._sync_action = None
            if rest is None:
                self._restart_if_audio_poisoned()
        return rest

    def finish_now(self):
        """Das ganze Diktat auf EINEM Thread abwickeln — für den Linux-Loop,
        der die Tastatur ohnehin einthreadig liest, und überall dort, wo es
        keinen Aktions-Thread gibt."""
        rest = self.finish()
        if rest is not None:
            rest()

    def _finish_capture(self):
        if self.partial:
            self.partial.stop()
            self.partial = None
        # Streamer und die zugehörige Zwischenablagen-Sicherung EINMAL übernehmen:
        # der Rest läuft lange, und ein neues Freihand-Diktat setzt self.streamer
        # inzwischen für SICH — ab hier zählt nur die lokale Kopie.
        streamer, clip_backup = self.streamer, self._clip_backup
        self.streamer = None
        # Eigenes Not-Aus-Ereignis für GENAU dieses Diktat. Ein Flag für alle
        # wäre falsch: ein neues Diktat würde den Not-Aus des vorherigen
        # aufheben, und dessen Text käme doch noch ins Fenster.
        panic = threading.Event()
        self._panic_flag = panic
        # Nachlauf: kurz weiter aufnehmen, damit das letzte Wort nicht abschneidet
        # (Bluetooth braucht mehr). Dann stoppen + Stopp-Ton.
        time.sleep((TAIL_PAD_BT_MS if self._bt else TAIL_PAD_MS) / 1000.0)
        t_stop = time.monotonic()
        # Der Rückgabewert ist die eben geschlossene Rohdatei: ein neues Diktat
        # schreibt in die andere, gelesen wird trotzdem diese hier.
        done_path = self.rec.stop()
        stop_took = time.monotonic() - t_stop
        if stop_took > SLOW_STOP:
            log("dict: rec.stop() dauerte %.1fs (Audiogerät träge?)" % stop_took)
        if self.cfg.beep:
            beep.stop()
        self.ducker.restore()
        data = self.rec.raw_bytes(done_path)
        raw_len = len(data)
        # Vorlauf verwerfen: mit VAD nur den eigenen Start-Ton (VAD macht den Rest
        # — sonst würde das erste Wort doppelt weggeschnitten); ohne VAD bei BT
        # den Profilwechsel-Müll großzügig.
        if self._vad:
            trim_ms = LEAD_TRIM_MS
        else:
            trim_ms = LEAD_TRIM_NOVAD_BT_MS if self._bt else LEAD_TRIM_MS
        trim = int(RATE * SAMPLE_BYTES * trim_ms / 1000)
        trim -= trim % SAMPLE_BYTES
        if len(data) > trim + 8000:
            data = data[trim:]
        log("dict: bt=%s vad=%s rec=%.2fs trim=%dms pad=%dms" % (
            self._bt, self._vad, raw_len / (RATE * SAMPLE_BYTES), trim_ms,
            TAIL_PAD_BT_MS if self._bt else TAIL_PAD_MS))
        if len(data) < 8000:  # < ~0,25 s Audio
            state_set("idle")
            notify(tr("too_short"))
            return None
        state_set("transcribing")
        return lambda: self._finish_rest(streamer, clip_backup, data, panic)

    def _finish_rest(self, streamer, clip_backup, data, panic):
        """Der lange Teil: Server, Transkription, Nachbearbeitung, Einfügen.
        Läuft auf dem Aktions-Thread und fasst den Recorder nicht mehr an."""
        try:
            self._transcribe_and_insert(streamer, clip_backup, data, panic)
        finally:
            self._restart_if_audio_poisoned()

    def _transcribe_and_insert(self, streamer, clip_backup, data, panic):
        # Beim Kaltstart lädt der Server erst das Modell; eine kurze Frist wäre
        # dort gleichbedeutend mit einem weggeworfenen Diktat.
        deadline = (SERVER_WAIT_FINISH if whisperclient.server_was_up()
                    else SERVER_WAIT_COLD)
        if not whisperclient.ensure_server(deadline=deadline):
            self._fail_with_rescue(data, streamer, clip_backup)
            return
        wav_from_raw(data, WAV)
        raw_text = whisperclient.transcribe(WAV, self.cfg)
        if raw_text is None:
            self._fail_with_rescue(data, streamer, clip_backup)
            return
        if panic.is_set():
            # „Diktat sofort beenden" wurde gedrückt, während transkribiert
            # wurde: kein Text mehr ins Fenster.
            log("panic: Ergebnis verworfen (Not-Aus während der Transkription)")
            if streamer is not None:
                streaming_restore(clip_backup)
            state_set("idle")
            return
        kind, value = textproc.postprocess(raw_text, self.cfg)
        if kind is None:
            if streamer is not None:
                streaming_restore(clip_backup)
            state_set("error", tr("nothing"))
            return
        if kind == "command":
            action = value
            # Im Streaming wurde das Kommando ("scratch that" / "press enter")
            # selbst live getippt -> diese Länge zurücknehmen.
            live_typed = len(streamer.typed) if streamer is not None else 0
            if streamer is not None:
                streaming_restore(clip_backup)
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
        if streamer is not None:
            # Streaming: KI auf den Endtext, dann Zielfenster exakt darauf bringen.
            final = self._ai_refine(mech) if self.cfg.ai_enabled else mech
            typed = streamer.finish(final)
            self.last_paste_len = len(typed)
            streaming_restore(clip_backup)
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

    # --------------------------------------------------- Rettung der Aufnahme
    def _fail_with_rescue(self, data, streamer, clip_backup):
        """Server nicht erreichbar oder Transkription gescheitert: die Aufnahme
        ist gesprochen und darf nicht einfach verschwinden — als WAV sichern und
        den Pfad in der Fehlermeldung nennen."""
        if streamer is not None:
            streaming_restore(clip_backup)
        path = self._rescue(data)
        text = tr("no_server")
        if path:
            text += " · " + tr("rescued").format(path=path)
        state_set("error", text)

    def _rescue(self, data):
        """Rohdaten als eigene WAV-Datei ablegen. Rückgabe: Pfad oder None."""
        os.makedirs(RUNDIR, exist_ok=True)
        # Der Name hat Sekundenauflösung. Bei totem Server scheitern zwei
        # Diktate leicht in derselben Sekunde — dann zählt ein Suffix weiter,
        # statt die erste Rettung stillschweigend zu überschreiben.
        stem = os.path.join(RUNDIR, time.strftime(RESCUE_NAME))
        path, n = stem + ".wav", 1
        while os.path.exists(path):
            path = "%s-%d.wav" % (stem, n)
            n += 1
        try:
            wav_from_raw(data, path)
        except OSError as exc:
            log("rescue: %s nicht schreibbar: %r" % (path, exc))
            return None
        log("rescue: Aufnahme gesichert -> %s" % path)
        self._prune_rescues()
        return path

    def _prune_rescues(self):
        """Nur die letzten RESCUE_KEEP Rettungen behalten — der Ordner ist eine
        Notfallablage, kein Archiv."""
        try:
            old = sorted(glob.glob(os.path.join(RUNDIR, RESCUE_GLOB)))
        except OSError:
            return
        for path in old[:-RESCUE_KEEP]:
            try:
                os.remove(path)
            except OSError:
                pass

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
        missing = check_permissions()
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
            time.sleep(RESCAN_EVERY)
            if self.cfg.reload():
                i18n.set_language(None if self.cfg.ui_language == "auto"
                                  else self.cfg.ui_language)
                # Hotkey-/Timing-Änderungen aus den Einstellungen greifen
                # ohne Neustart (wie im Linux-Loop)
                listener.reconfigure(self.cfg.chord, self.cfg.hold_min,
                                     self.cfg.double_window)
            self._sync_wakeword()
            self._mac_watchdog(listener)

    def _install_panic_signal(self):
        """Not-Aus per Signal (SIGUSR2): beendet ein hängendes Diktat auch
        dann, wenn der Event-Tap nicht mehr reagiert — der Menüpunkt „Diktat
        sofort beenden" schickt genau dieses Signal an den Daemon-Prozess."""
        if not hasattr(signal, "SIGUSR2"):
            return
        try:
            signal.signal(signal.SIGUSR2, lambda *_: self.panic_stop())
        except (OSError, ValueError):   # nicht im Haupt-Thread -> kein Handler
            log("mac: SIGUSR2-Handler nicht installierbar (Not-Aus nur übers Menü)")

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

    def _run_linux(self):
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
                # Linux liest die Tastatur einthreadig: hier läuft auch der
                # lange Teil, wie bisher, mitten in dieser Schleife.
                self.finish_now()


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
    log("quassel-daemon %s gestartet (%s, pid=%d)"
        % (__version__, time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    try:
        Daemon().run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
