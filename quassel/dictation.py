"""Gemeinsame Ablaufsteuerung eines Diktats für Linux, macOS und Windows.

Ein Diktat läuft überall gleich ab: Aufnahme starten, beim Loslassen kurz
nachlaufen lassen, stoppen, Rohdaten lesen, dann Server, Transkription,
Nachbearbeitung, Einfügen, Verlauf. Diese Reihenfolge und ihre Garantien
stehen hier EINMAL. Was je Plattform verschieden ist (Aufnahme, Einfügen,
Ducking, Benachrichtigung, Anzeige), liefert die Unterklasse über die
Methoden im Abschnitt „Plattform-Anbindung" (daemon.Daemon für Linux und
macOS, win.app.WinDictation für Windows).

Thread-Garantien (gelten für jede Plattform):

* start_recording(), cancel_recording() und finish() laufen auf DEMSELBEN
  Thread, dem Ereignis-Thread des Hotkeys. Damit liegt das Beenden einer
  Aufnahme strukturell vor dem Start der nächsten — kein Schloss muss diese
  Reihenfolge herstellen, ein gemeinsamer Thread schon.
* finish() erledigt nur den synchronen Teil (Nachlauf, Recorder stoppen,
  Rohdaten lesen) und gibt alles Langsame als Callable zurück. Dieser Rest
  läuft auf dem Aktions-Thread und fasst den Recorder nicht mehr an.
* Der Recorder schreibt je Aufnahme abwechselnd in zwei Rohdateien; der
  Rest liest die Datei, die SEIN rec.stop() genannt hat.
* panic_stop() ist aus jedem Thread aufrufbar und darf an keinem Schritt
  scheitern.
"""
import glob
import os
import queue
import threading
import time

from . import (aimodes, beep, cleanup as cleanup_module, config, dictprompt, i18n, progmode, stats, textproc,
               textreplace, vad, wakeword, whisperclient)
from .audio import RATE, SAMPLE_BYTES
from .i18n import tr
from .state import RUNDIR, WAV
from .streaming import StreamTyper
from .win.machine import ChordMachine

# Am Diktat-Ende wartet niemand zwei Minuten auf einen Server, der ohnehin
# schon lief. Beim Kaltstart lädt er dagegen erst das Modell — dort wird lange
# gewartet, statt ein fertiges Diktat wegzuwerfen.
SERVER_WAIT_FINISH = 30
SERVER_WAIT_COLD = 600
# Gerettete Aufnahmen: Server tot oder Transkription gescheitert.
RESCUE_NAME = "rescued-%Y%m%d-%H%M%S"       # via time.strftime, ohne Endung
RESCUE_GLOB = "rescued-*.wav"
RESCUE_KEEP = 5
SLOW_STOP = 0.5        # s: ab hier ist das Stoppen der Aufnahme meldenswert
MIN_AUDIO_BYTES = 8000  # < ~0,25 s Audio gilt als zu kurz
PARTIAL_EVERY = 2.0    # s: Mindestabstand der Live-Vorschau-Transkripte
PARTIAL_WINDOW = 15    # s: Vorschau nutzt nur die letzten N Sekunden Audio
# Obergrenze für den adaptiven Abstand (PartialLoop.run): ein einzelner
# langsamer Durchlauf (Server-Kaltstart, kurzer Systemhänger) soll die
# Vorschau nicht minutenlang verstummen lassen. 10 s = Fünffaches des
# Mindestabstands -- genug Puffer für eine kurze Verlangsamung, aber die
# Vorschau meldet sich spätestens nach 10 s wieder.
PARTIAL_MAX_WAIT = 10.0
# Halten-Modus: Vorschau erst ab dieser Aufnahmedauer (Audit C44). whisper-
# server arbeitet Anfragen nacheinander ab; läuft beim Loslassen gerade ein
# Teiltranskript, wartet das Finale dahinter — auf der CPU Sekunden. Kurze
# Halten-Diktate (die meisten) enden sonst oft genau während der ersten
# Vorschau bei 2 s. Freihand-Diktate sind lang, dort bleibt die Vorschau.
PREVIEW_HOLD_MIN = 4.0

# Vorlauf verwerfen + Ende puffern. WICHTIG: läuft VAD (serverseitig), entfernt
# es Stille/BT-Ramp am Anfang selbst — dann NUR unseren eigenen Start-Ton
# wegschneiden (sonst doppelt geschnitten -> erstes Wort weg). Ohne VAD den
# BT-Profilwechsel-Müll am Anfang großzügig trimmen.
LEAD_TRIM_MS = 160            # nur der Start-Ton (mit VAD)
LEAD_TRIM_NOVAD_BT_MS = 400   # ohne VAD: BT-Ramp am Anfang
TAIL_PAD_MS, TAIL_PAD_BT_MS = 250, 450

# So lange bleibt der Text einer Meldung in state.json stehen: länger als die
# Pille ihn zeigt (3 s) und als ihr Abfragetakt (80 ms), danach wird er
# geleert (Audit A6).
STATE_TEXT_KEEP = 4.0
ACTION_STALL = 60.0    # s: so lange darf der lange Rest eines Diktats höchstens laufen
PUMP_POLL = 0.02       # s: Takt des Ereignis-Threads (Doppeltipp-Fenster, Einfügen)

# Eigene Rohdatei für den Wake-Listener (getrennt vom Tastatur-Pfad); seine
# WAV ist eine Zwischendatei dieses Prozesses (scratch_wav("wake")).
WAKE_RAW = os.path.join(os.path.dirname(WAV), "wake.raw")


def postprocess_transcript(raw_text, cfg, language=None, pcm=None):
    """Bekannte Stille-Antworten wie ein leeres Diktat behandeln."""
    if dictprompt.is_silence_hallucination(raw_text, pcm=pcm):
        return None, None
    if language is None:
        return textproc.postprocess(raw_text, cfg)
    cfg_with_language = type("CfgWithLanguage", (), {
        "commands": getattr(cfg, "commands", True),
        "punctuation": getattr(cfg, "punctuation", True),
        "language": language,
    })()
    return textproc.postprocess(raw_text, cfg_with_language)


def wake_transcribe(cfg, pcm, wav_path, wav_from_raw, log):
    """Eine Wake-Äußerung transkribieren. Als Funktion, damit Daemon und
    Windows dieselbe Fassung nutzen und der Log-Inhalt an einer Stelle
    geprüft wird."""
    if not pcm or not whisperclient.ensure_server():
        return None
    try:
        try:
            wav_from_raw(pcm, wav_path)
        except OSError:
            return None
        # Wake-Phrase als Bias mitgeben, damit Whisper das Kunstwort eher trifft.
        text = whisperclient.transcribe(wav_path, cfg, timeout=30,
                                        prompt_extra=cfg.wakeword_phrase)
    finally:
        discard(wav_path)          # alles im Raum Gesprochene: nicht liegen lassen
    # Nur Länge und Treffer loggen: der Listener hört alles im Raum, der Text
    # darf nie in eine Logdatei.
    heard = (text or "").strip()
    matched, _ = wakeword.match_wake(heard, cfg.wakeword_phrase)
    log("wake: heard %d chars, match=%s" % (len(heard), "yes" if matched else "no"))
    return text


def clipboard_only_text():
    """Hinweis, wenn der Text nur in der Zwischenablage liegt. Nutzt den
    i18n-Schlüssel, sobald es ihn gibt; bis dahin beide Sprachen hier."""
    if "paste_clipboard_only" in i18n.STRINGS:
        return tr("paste_clipboard_only")
    if i18n.current() == "de":
        return "Text liegt in der Zwischenablage – Strg+V drücken"
    return "Text is on the clipboard – press Ctrl+V"


def scratch_wav(name):
    """Pfad einer Zwischen-WAV dieses Prozesses (Diktat, Vorschau). Die
    Prozessnummer im Namen sorgt dafür, dass beim Aufräumen nie die Datei
    eines anderen Quassel-Prozesses (zweites Profil, Test) verschwindet."""
    return os.path.join(os.path.dirname(WAV), "%s-%d.wav" % (name, os.getpid()))


def discard(path):
    """Eine Aufnahme- oder Zwischendatei löschen (Audit A6). Nur absolute
    Pfade; fehlt die Datei, ist das kein Fehler."""
    if not isinstance(path, str) or not os.path.isabs(path):
        return
    try:
        os.remove(path)
    except OSError:
        pass


class PartialLoop(threading.Thread):
    """Erzeugt während der Aufnahme Teiltranskripte für Live-Vorschau und
    (im Freihand-Modus mit aktivem Streaming) für das Live-Tippen.

    owner: die Ablaufsteuerung (DictationCore) — streamer wird ggf. mitten im
    Lauf gesetzt, Anzeige und WAV-Schreiben laufen über ihre Plattform-
    Anbindung."""

    def __init__(self, rec, cfg, owner, wav_path=None):
        super().__init__(daemon=True)
        self.rec, self.cfg = rec, cfg
        self.owner = owner
        self.wav_path = wav_path
        self.stop_event = threading.Event()
        # Seit wann läuft ein Teiltranskript am Server (None = keins)? Das
        # Finale wartet dahinter; finish() schreibt die Dauer ins Log.
        self.inflight_since = None

    def run(self):
        wav_path = self.wav_path or scratch_wav("partial")
        try:
            self._loop(wav_path)
        finally:
            # Die Vorschau-WAV enthält die letzten Sekunden des Diktats.
            discard(wav_path)

    def _loop(self, wav_path):
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
                if not getattr(self.owner, "_handsfree", False) and \
                        time.monotonic() - self.rec.started < PREVIEW_HOLD_MIN:
                    continue
                data = self.rec.raw_bytes()
                if len(data) < RATE * SAMPLE_BYTES // 2:   # < 0,5 s
                    continue
                streamer = self.owner.streamer
                end_byte = len(data)
                max_bytes = RATE * SAMPLE_BYTES * PARTIAL_WINDOW
                start_byte = (streamer.audio_window(end_byte, max_bytes)
                              if streamer is not None else max(0, end_byte - max_bytes))
                data = data[start_byte:end_byte]
                try:
                    self.owner._wav_from_raw(data, wav_path)
                except OSError:
                    continue
                # Erneut prüfen, unmittelbar VOR der teuren Transkription: das
                # Diktat kann zwischen Schleifenstart und hier beendet worden
                # sein. Ohne diese zweite Prüfung würde ein Teiltranskript
                # starten, obwohl das Finale schon ansteht -- und sich hinter
                # ihm in die Warteschlange des Servers stellen.
                if self.stop_event.is_set() or not self.rec.active:
                    return
                self.inflight_since = time.monotonic()
                try:
                    prompt = streamer.prompt_context() if streamer is not None else None
                    try:
                        if prompt:
                            result = whisperclient.transcribe_result(
                                wav_path, self.cfg, timeout=20, prompt_extra=prompt)
                        else:
                            result = whisperclient.transcribe_result(wav_path, self.cfg, timeout=20)
                    except (OSError, TimeoutError):
                        continue  # server failure: keep confirmed text, retry later
                finally:
                    self.inflight_since = None
                raw = result.text if result is not None else None
                # Ehrlich bleiben: die HTTP-Anfrage selbst lässt sich
                # clientseitig nicht abbrechen, sie läuft am Server so oder so
                # durch, und das Finale wartet trotzdem dahinter. Hier wird
                # nur noch das ERGEBNIS verworfen, kein Zeitgewinn erzielt.
                if raw is None or self.stop_event.is_set() or not self.rec.active:
                    continue
                kind, text = postprocess_transcript(
                    raw, self.cfg, result.language or getattr(self.cfg, "language", "auto"))
                if kind != "text":
                    continue
                streamer = self.owner.streamer
                show = self.cfg.pill_preview
                if streamer is not None:
                    streamer.update(text, audio_start=start_byte, audio_end=end_byte)
                    # Blase zeigt nur den noch nicht getippten Rest (oder nichts)
                    self.owner._state("recording",
                                      streamer.prev_partial[len(streamer.typed):].strip()
                                      if show else "")
                else:
                    self.owner._state("recording", text if show else "")
            finally:
                delay = max(PARTIAL_EVERY,
                            min(time.monotonic() - iter_start, PARTIAL_MAX_WAIT))

    def stop(self):
        self.stop_event.set()


class DictationCore:
    """Ablauf eines Diktats ohne Plattformbezug. Unterklassen liefern die
    Plattform-Anbindung (siehe unten) und setzen cfg, rec und ducker."""

    # Klassenattribute als Vorgabe: Tests bauen Instanzen über __new__, ohne
    # __init__, und setzen nur, was sie brauchen.
    partial = None
    streamer = None             # aktiv nur im Freihand-Modus mit Streaming
    _clip_backup = None
    last_paste_len = 0
    wake = None                 # WakeListener-Thread (nur wenn Wake-Word an)
    _bt = False                 # ist die aktive Aufnahmequelle Bluetooth?
    _vad = False                # läuft serverseitig VAD? (dann Vorlauf nicht doppelt trimmen)
    # Not-Aus-Ereignis des gerade laufenden Diktats (je Diktat eines, siehe
    # _finish_capture) und Name des laufenden synchronen Teils.
    _panic_flag = None
    _sync_action = None
    _runner = None              # ActionRunner, wo die Plattform einen nutzt
    _handsfree = False          # Freihand-Modus (Doppeltipp) im laufenden Diktat

    # ------------------------------------------------- Plattform-Anbindung
    # Jede Methode hier ist bewusst schmal: sie reicht an die Plattform weiter.
    def _state(self, state, text=""):
        """Zustand an Pille/Tray melden (idle|recording|transcribing|done|error)."""
        raise NotImplementedError

    def _notify(self, text):
        raise NotImplementedError

    def _log(self, msg):
        raise NotImplementedError

    def _paste(self, text):
        raise NotImplementedError

    def _send_backspaces(self, n):
        raise NotImplementedError

    def _send_enter(self):
        raise NotImplementedError

    def _type_chunk(self, chunk):
        raise NotImplementedError

    def _streaming_begin(self):
        raise NotImplementedError

    def _streaming_restore(self, backup):
        raise NotImplementedError

    def _wav_from_raw(self, data, path):
        raise NotImplementedError

    def _new_partial(self):
        return PartialLoop(self.rec, self.cfg, self)

    def _rundir(self):
        """Ablage für gerettete Aufnahmen."""
        return RUNDIR

    def _new_recorder(self, raw_path):
        """Eigener Recorder für den Wake-Listener."""
        raise NotImplementedError

    def _mic_is_bluetooth(self):
        return False

    def _server_vad(self):
        return False

    def _report_mic_missing(self):
        """rec.start() scheiterte: dem Nutzer sagen, was fehlt."""
        self._notify(tr("no_mic"))

    def _tail_pad_ms(self):
        return TAIL_PAD_BT_MS if self._bt else TAIL_PAD_MS

    def _lead_trim_ms(self):
        # Vorlauf verwerfen: mit VAD nur den eigenen Start-Ton (VAD macht den
        # Rest — sonst würde das erste Wort doppelt weggeschnitten); ohne VAD
        # bei BT den Profilwechsel-Müll großzügig.
        if self._vad:
            return LEAD_TRIM_MS
        return LEAD_TRIM_NOVAD_BT_MS if self._bt else LEAD_TRIM_MS

    def _forget_text(self, token):
        """Den Text einer eben gemeldeten Meldung nach der Anzeige aus der
        Zustandsdatei nehmen. token ist der Rückgabewert von _state (None:
        die Plattform hält keinen Text auf der Platte)."""
        if token is None:
            return
        timer = threading.Timer(STATE_TEXT_KEEP, self._clear_state_text, (token,))
        timer.daemon = True
        timer.start()

    def _clear_state_text(self, token):
        """Plattform: den Text der Meldung token leeren."""

    def _after_audio_released(self):
        """Nach jedem Ende einer Aufnahme (auch Abbruch, zu kurz). Der Daemon
        prüft hier, ob CoreAudio verklemmt ist."""

    def _after_panic(self):
        """Nach dem Not-Aus, aus beliebigem Thread."""

    def _reset_hotkey(self):
        """Chord-Maschine in den Ruhezustand zwingen (nach dem Not-Aus)."""

    def _action_running(self):
        """Läuft gerade der lange Rest eines Diktats auf dem Aktions-Thread?"""
        runner = self._runner
        return runner is not None and runner.current_action is not None

    def _wake_busy(self):
        return self.rec.active

    # ------------------------------------------------------------- Aufnahme
    def _refresh_cfg(self):
        self.cfg.reload()
        i18n.set_language(None if self.cfg.ui_language == "auto" else self.cfg.ui_language)
        # Vor dem Startton, damit eine im Kontrollzentrum geänderte Gerätewahl
        # schon beim nächsten Diktat greift und nicht erst nach einem Neustart.
        beep.set_output(self.cfg.beep_output)

    def start_recording(self):
        self._refresh_cfg()
        # Starten und Beenden laufen auf DEMSELBEN Thread (dem Ereignis-Thread
        # des Hotkeys, im Linux-Loop der Hauptschleife): die Reihenfolge steht
        # damit strukturell fest, kein Schloss muss sie herstellen.
        if not self.rec.start(self.cfg.mic):
            self._report_mic_missing()
            return False
        if self.cfg.beep:
            beep.start()                 # aufsteigender Ton: jetzt sprechen
        self._bt = self._mic_is_bluetooth()
        self._vad = self._server_vad()
        self.ducker.apply(self.cfg.mute_mode)
        self.streamer = None
        self._handsfree = False
        self.partial = self._new_partial()
        self.partial.start()
        self._state("recording")
        return True

    def enable_streaming(self):
        """Beim Wechsel in den Freihand-Modus: Streaming starten, falls
        eingeschaltet. (Im Halten-Modus technisch unmöglich -> nie hier.)"""
        self._handsfree = True
        if (self.streamer is not None or not self.cfg.streaming
                or self._cleanup_active() or self.cfg.ai_enabled):
            return
        self._clip_backup = self._streaming_begin()
        self.streamer = StreamTyper(self.cfg.streaming_mode,
                                    self._type_chunk, self._send_backspaces)

    def cancel_recording(self, reason_key):
        # Wie finish(): läuft synchron auf dem Ereignis-Thread und zählt
        # solange als laufendes Diktat (siehe _busy).
        self._sync_action = "cancel"
        try:
            if self.partial:
                self.partial.stop()
                self.partial = None
            if self.streamer is not None:
                self._streaming_restore(self._clip_backup)
                self.streamer = None
            discard(self.rec.stop())     # verworfenes Diktat: Ton gleich mit
            if self.cfg.beep:
                beep.stop()
            self.ducker.restore()
            self._state("idle")
            self._notify("✖ " + tr(reason_key))
        finally:
            self._sync_action = None
        # Ein Stream, der beim Abbruch aufgegeben werden musste, vergiftet den
        # Prozess genauso wie einer am Diktat-Ende.
        self._after_audio_released()

    def panic_stop(self, *_args):
        """Not-Aus: laufende Aufnahme sofort beenden, ohne zu transkribieren.

        Aus JEDEM Thread aufrufbar, auch aus einem Signal-Handler. Jeder
        Schritt hier hat eine harte Frist (Recorder-Stopp, osascript), damit
        der Not-Aus nicht genau dort hängen bleibt, wo das Diktat schon hängt.
        Läuft weder eine Aufnahme noch eine Aktion, gibt es nichts zu beenden
        — dann sagt die Methode das und tut sonst nichts."""
        if not self._busy():
            self._log("panic: Not-Aus ohne laufendes Diktat")
            self._notify(tr("nothing_running"))
            return
        # Merker des LAUFENDEN Diktats: wer „sofort beenden" drückt, will von
        # diesem Diktat keinen Text mehr. Ein später begonnenes Diktat hat sein
        # eigenes Ereignis und bleibt davon unberührt.
        if self._panic_flag is not None:
            self._panic_flag.set()
        self._log("panic: Not-Aus -> laufendes Diktat wird abgebrochen")
        if self.partial:
            self.partial.stop()
            self.partial = None
        if self.streamer is not None:
            self._streaming_restore(self._clip_backup)
            self.streamer = None
        try:
            self.rec.stop()
        except Exception as exc:   # noqa: BLE001 — der Not-Aus darf an nichts scheitern
            self._log("panic: rec.stop() fehlgeschlagen: %r" % exc)
        if self.cfg.beep:
            beep.stop()
        self.ducker.restore()
        self._state("idle")
        self._notify(tr("panic_stopped"))
        # Die Maschine steht nach einem Not-Aus womöglich noch auf "toggle" —
        # der nächste Chord-Druck liefe sonst in ein finish() ohne Aufnahme.
        self._reset_hotkey()
        # Hängt der Rest (Server, Einfügen), bliebe jedes weitere Diktat
        # dahinter stehen: zurücklassen, ein frischer Aktions-Thread übernimmt.
        runner = self._runner
        if runner is not None:
            runner.abandon()
        self._after_panic()

    def _busy(self):
        """Läuft gerade ein Diktat? Drei Zustände zählen dazu: die Aufnahme
        selbst, der synchrone Teil des Beendens (zwischen rec.stop() und der
        Übergabe an den Aktions-Thread — dort ist die Aufnahme schon aus und
        die Aktion noch nicht gestartet) und der lange Rest."""
        if self.rec.active or self._sync_action is not None:
            return True
        return self._action_running()

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
                self._after_audio_released()
        return rest

    def finish_now(self):
        """Das ganze Diktat auf EINEM Thread abwickeln — überall dort, wo es
        keinen Aktions-Thread gibt."""
        rest = self.finish()
        if rest is not None:
            rest()

    def _finish_capture(self):
        if self.partial:
            # C44: läuft gerade ein Teiltranskript, wartet das Finale dahinter.
            # Die Anfrage selbst lässt sich nicht zurückholen; festhalten, wie
            # oft und wie lange das vorkommt.
            since = getattr(self.partial, "inflight_since", None)
            if since is not None:
                self._log("dict: Vorschau lief beim Loslassen seit %.2fs"
                          % (time.monotonic() - since))
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
        pad_ms = self._tail_pad_ms()
        time.sleep(pad_ms / 1000.0)
        t_stop = time.monotonic()
        # Der Rückgabewert ist die eben geschlossene Rohdatei: ein neues Diktat
        # schreibt in die andere, gelesen wird trotzdem diese hier.
        done_path = self.rec.stop()
        stop_took = time.monotonic() - t_stop
        if stop_took > SLOW_STOP:
            self._log("dict: rec.stop() dauerte %.1fs (Audiogerät träge?)" % stop_took)
        if self.cfg.beep:
            beep.stop()
        self.ducker.restore()
        data = self.rec.raw_bytes(done_path)
        raw_len = len(data)
        trim_ms = self._lead_trim_ms()
        trim = int(RATE * SAMPLE_BYTES * trim_ms / 1000)
        trim -= trim % SAMPLE_BYTES
        if len(data) > trim + MIN_AUDIO_BYTES:
            data = data[trim:]
        self._log("dict: bt=%s vad=%s rec=%.2fs trim=%dms pad=%dms" % (
            self._bt, self._vad, raw_len / (RATE * SAMPLE_BYTES), trim_ms, pad_ms))
        if len(data) < MIN_AUDIO_BYTES:
            self._state("idle")
            self._notify(tr("too_short"))
            discard(done_path)
            return None
        self._state("transcribing")
        return lambda: self._finish_rest(streamer, clip_backup, data, panic, done_path)

    def _finish_rest(self, streamer, clip_backup, data, panic, done_path=None):
        """Der lange Teil: Server, Transkription, Nachbearbeitung, Einfügen.
        Läuft auf dem Aktions-Thread und fasst den Recorder nicht mehr an —
        nur die Rohdatei SEINES Diktats wird am Ende gelöscht: die Daten
        liegen ab hier im Speicher, und eine gerettete Aufnahme hat ihre
        eigene Datei. Scheitert die Rettung, bleibt die Rohdatei stehen."""
        keep_raw = True
        try:
            keep_raw = self._transcribe_and_insert(streamer, clip_backup, data, panic) is False
        finally:
            if not keep_raw:
                discard(done_path)
            self._after_audio_released()

    def _transcribe_and_insert(self, streamer, clip_backup, data, panic):
        # Beim Kaltstart lädt der Server erst das Modell; eine kurze Frist wäre
        # dort gleichbedeutend mit einem weggeworfenen Diktat.
        deadline = (SERVER_WAIT_FINISH if whisperclient.server_was_up()
                    else SERVER_WAIT_COLD)
        if not whisperclient.ensure_server(deadline=deadline):
            return self._fail_with_rescue(data, streamer, clip_backup)
        wav = scratch_wav("rec")
        try:
            self._wav_from_raw(data, wav)
            result = whisperclient.transcribe_result(wav, self.cfg)
        finally:
            discard(wav)
        raw_text = result.text
        if raw_text is None:
            return self._fail_with_rescue(data, streamer, clip_backup)
        if panic.is_set():
            # „Diktat sofort beenden" wurde gedrückt, während transkribiert
            # wurde: kein Text mehr ins Fenster.
            self._log("panic: Ergebnis verworfen (Not-Aus während der Transkription)")
            if streamer is not None:
                self._streaming_restore(clip_backup)
            self._state("idle")
            return
        kind, value = postprocess_transcript(
            raw_text, self.cfg,
            result.language or getattr(self.cfg, "language", "auto"), data)
        if kind is None:
            if streamer is not None:
                self._streaming_restore(clip_backup)
            self._state("error", tr("nothing"))
            return
        if kind == "command":
            action = value
            # Im Streaming wurde das Kommando ("scratch that" / "press enter")
            # selbst live getippt -> diese Länge zurücknehmen.
            live_typed = len(streamer.typed) if streamer is not None else 0
            if streamer is not None:
                self._streaming_restore(clip_backup)
            if action == "enter":
                if live_typed:
                    self._send_backspaces(live_typed)
                self._send_enter()
                self.last_paste_len = 0
                self._state("done", tr("pressed_enter"))
                return
            # action == "undo": im Streaming das live Getippte, sonst letztes Diktat
            undo = live_typed if live_typed else self.last_paste_len
            if undo > 0:
                self._send_backspaces(undo)
                self.last_paste_len = 0
                self._state("done", tr("deleted"))
            else:
                self._state("error", tr("nothing"))
            return
        mechanical_failed = False
        try:
            mech = self._refine_mechanical(value)
        except Exception:
            if not self._cleanup_active():
                raise
            mech = value
            mechanical_failed = True
        inserted = True             # Streaming tippt selbst, ohne Rückmeldung
        if self._cleanup_active() and streamer is None:
            # B19: finish all cleanup and validation before one target insert.
            final = value if mechanical_failed else self._cleanup_refine(value, mech)
            if panic.is_set():
                self._state("idle")
                return
            inserted = self._insert(final)
            self.last_paste_len = len(final) if inserted else 0
            value = final
        elif streamer is not None:
            # A stream already touched the target; never run a later model
            # request that could replace it after focus has changed.
            final = mech
            typed = streamer.finish(final)
            self.last_paste_len = len(typed)
            self._streaming_restore(clip_backup)
            value = final
        elif self.cfg.ai_enabled:
            # B19: keine Backspaces nach dem Netzaufruf in ein inzwischen
            # anderes Zielfenster schicken. Erst fertig berechnen, einmal einfügen.
            self._state("transcribing")
            final = self._ai_refine(mech)
            if panic.is_set():
                self._state("idle")
                return
            inserted = self._insert(final)
            self.last_paste_len = len(final) if inserted else 0
            value = final
        else:
            inserted = self._insert(mech)
            self.last_paste_len = len(mech) if inserted else 0
            value = mech
        if inserted:
            self._forget_text(self._state("done", value))
        else:
            self._report_clipboard_only()
        secs = len(data) / (RATE * SAMPLE_BYTES)
        self._after_insert(value, secs)

    # ------------------------------------------------------------ Einfügen
    def _insert(self, text):
        """Text einfügen. False nur, wenn die Plattform ausdrücklich meldet,
        dass der Tastendruck nicht ankam und der Text nur in der
        Zwischenablage liegt (Linux ohne laufendes ydotoold, Audit D57).
        Plattformen ohne Rückmeldung (None) gelten als erfolgreich."""
        return self._paste(text) is not False

    def _report_clipboard_only(self):
        """Die Pille sagte sonst „fertig", obwohl im Fenster nichts ankam."""
        self._log("paste: Tastendruck nicht angekommen, Text nur in der Zwischenablage")
        self._state("error", clipboard_only_text())

    # --------------------------------------------------- Rettung der Aufnahme
    def _fail_with_rescue(self, data, streamer, clip_backup):
        """Server nicht erreichbar oder Transkription gescheitert: die Aufnahme
        ist gesprochen und darf nicht einfach verschwinden — als WAV sichern und
        den Pfad in der Fehlermeldung nennen. Rückgabe False, wenn auch die
        Rettung scheiterte (dann bleibt die Rohdatei als letzte Kopie)."""
        if streamer is not None:
            self._streaming_restore(clip_backup)
        path = self._rescue(data)
        text = tr("no_server")
        if path:
            text += " · " + tr("rescued").format(path=path)
        self._state("error", text)
        return path is not None

    def _rescue(self, data):
        """Rohdaten als eigene WAV-Datei ablegen. Rückgabe: Pfad oder None."""
        rundir = self._rundir()
        os.makedirs(rundir, exist_ok=True)
        # Der Name hat Sekundenauflösung. Bei totem Server scheitern zwei
        # Diktate leicht in derselben Sekunde — dann zählt ein Suffix weiter,
        # statt die erste Rettung stillschweigend zu überschreiben.
        stem = os.path.join(rundir, time.strftime(RESCUE_NAME))
        path, n = stem + ".wav", 1
        while os.path.exists(path):
            path = "%s-%d.wav" % (stem, n)
            n += 1
        try:
            self._wav_from_raw(data, path)
        except OSError as exc:
            self._log("rescue: %s nicht schreibbar: %r" % (path, exc))
            return None
        self._log("rescue: Aufnahme gesichert -> %s" % path)
        self._prune_rescues()
        return path

    def _prune_rescues(self):
        """Nur die letzten RESCUE_KEEP Rettungen behalten — der Ordner ist eine
        Notfallablage, kein Archiv."""
        try:
            old = sorted(glob.glob(os.path.join(self._rundir(), RESCUE_GLOB)))
        except OSError:
            return
        for path in old[:-RESCUE_KEEP]:
            try:
                os.remove(path)
            except OSError:
                pass

    # ------------------------------------------------------ Nachbearbeitung
    def _refine_mechanical(self, text):
        """Schnelle lokale Umformungen ohne Netz/KI: Programmier-Diktat + Textersetzungen."""
        if self.cfg.programmer_mode:
            text = progmode.apply_if_addressed(text)
        if self.cfg.text_replace:
            rules = config.replacement_rules()
            if rules:
                text = textreplace.apply_rules(text, rules)
        return text

    def _refine(self, text):
        """Mechanische Umformungen + (optional) lokale KI — synchron (Wake-Pfad)."""
        raw = text
        if self._cleanup_active():
            try:
                text = self._refine_mechanical(text)
            except Exception:
                return raw
            return self._cleanup_refine(raw, text)
        text = self._refine_mechanical(text)
        if self.cfg.ai_enabled:
            text = self._ai_refine(text)
        return text

    def _cleanup_active(self):
        return (getattr(self.cfg, "cleanup_enabled", False)
                and getattr(self.cfg, "cleanup_level", "aus") != "aus")

    def _cleanup_refine(self, raw, mechanical):
        """Keep addressed AI modes; otherwise validate cleanup before insert."""
        try:
            if self.cfg.ai_enabled and self.cfg.ai_voice_modes:
                mode, _ = aimodes.detect_voice_mode(mechanical)
                if mode is None:
                    mode, _ = self._detect_custom_voice(mechanical)
                if mode is not None:
                    transformed = self._ai_refine(mechanical)
                    return raw if transformed == mechanical else transformed
            result = cleanup_module.cleanup(mechanical, self.cfg.cleanup_level, self.cfg)
            return raw if result.fallback_reason else result.text
        except Exception:
            return raw

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
        return aimodes.detect_custom_voice_mode(
            text, getattr(self.cfg, "ai_modes_text", "") or "")

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
        if self.cfg.auto_learn and not self.cfg.programmer_mode:
            try:
                config.dictionary_learn(value)
            except Exception:    # noqa: BLE001 — Lernen darf nie stören
                pass

    # ----------------------------------------------------- Wake-Word (opt-in)
    def _sync_wakeword(self):
        """Listener nach Konfig starten/stoppen (idempotent)."""
        if self.cfg.wakeword_enabled and self.wake is None:
            self.wake = wakeword.WakeListener(
                self.cfg, self._wake_record_utterance, self._wake_transcribe,
                self._wake_insert, is_busy=self._wake_busy)
            self.wake.start()
        elif not self.cfg.wakeword_enabled and self.wake is not None:
            self.wake.stop()
            self.wake = None

    def _wake_record_utterance(self):
        """Eine Äußerung über VAD aufnehmen (für den Wake-Listener). Eigener
        Recorder + eigene Rohdatei, damit der Tastatur-Pfad und die Pille
        ungestört bleiben. Schwelle wird am Grundrauschen kalibriert."""
        rec = self._new_recorder(WAKE_RAW)
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
        # Der Wake-Listener hört alles im Raum: die Rohdatei nicht liegen lassen.
        discard(rec.raw_path)
        started = det.speech_started if det is not None else False
        self._log("wake: rec %d bytes, peak_rms=%.0f, speech=%s" % (len(data), peak, started))
        if not started or len(data) < MIN_AUDIO_BYTES:
            return None
        return data

    def _wake_transcribe(self, pcm):
        return wake_transcribe(self.cfg, pcm, scratch_wav("wake"),
                               self._wav_from_raw, self._log)

    def _wake_insert(self, raw_text):
        kind, value = postprocess_transcript(raw_text, self.cfg)
        if kind != "text" or not value.strip():
            return
        value = self._refine(value)
        if self._insert(value):
            self.last_paste_len = len(value)
            self._forget_text(self._state("done", value))
        else:
            self.last_paste_len = 0
            self._report_clipboard_only()
        self._after_insert(value)


class ActionRunner:
    """Aktions-Thread: führt die langen Reste der Diktate (Transkription,
    Nachbearbeitung, Einfügen) der Reihe nach aus. Hängt einer, bleibt der
    Ereignis-Thread frei; current_action und last_action_tick zeigen, woran
    der Thread gerade sitzt.

    abandon() ist der Kern des Not-Aus: ein hängender Rest lässt sich in
    Python nicht abbrechen, aber zurücklassen. Ein frischer Thread übernimmt
    die Warteschlange, der alte läuft ins Leere und endet danach still; sein
    Ergebnis verwirft das Not-Aus-Ereignis des Diktats."""

    def __init__(self, name="dictation-actions", log=None):
        self.name = name
        self._log = log or (lambda msg: None)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._queue = queue.Queue()
        self._gen = 0
        self.current_action = None
        self.last_action_tick = time.monotonic()
        self._spawn()

    def _spawn(self):
        self._gen += 1
        threading.Thread(target=self._run, args=(self._gen, self._queue),
                         daemon=True, name=self.name).start()

    @property
    def generation(self):
        return self._gen

    def submit(self, name, fn):
        self._queue.put((name, fn))

    def _run(self, gen, q):
        while not self._stopped.is_set():
            try:
                item = q.get(timeout=0.5)
            except queue.Empty:
                if gen != self._gen:
                    return
                self.last_action_tick = time.monotonic()
                continue
            with self._lock:
                if gen != self._gen:        # zurückgelassen: Auftrag weiterreichen
                    self._queue.put(item)
                    return
                name, fn = item
                self.current_action = name
                self.last_action_tick = time.monotonic()
            # Eine geworfene Ausnahme darf den Thread nicht töten — sonst
            # stünde nach einem Fehler jedes weitere Diktat still.
            try:
                fn()
            except Exception as exc:   # noqa: BLE001
                self._log("actions: %r fehlgeschlagen: %r" % (name, exc))
            finally:
                with self._lock:
                    if gen == self._gen:
                        self.current_action = None
                        self.last_action_tick = time.monotonic()
            if gen != self._gen:
                return

    def abandon(self):
        """Laufenden Rest zurücklassen und einen frischen Thread starten.
        True = es lief einer."""
        with self._lock:
            if self.current_action is None:
                return False
            self._log("actions: %r wird zurückgelassen (Not-Aus)" % self.current_action)
            old, self._queue = self._queue, queue.Queue()
            while True:
                try:
                    self._queue.put(old.get_nowait())
                except queue.Empty:
                    break
            self.current_action = None
            self.last_action_tick = time.monotonic()
            self._spawn()
        return True

    def run_blocking(self, name, fn, on_stall=None, stall_after=ACTION_STALL, poll=0.1):
        """fn auf dem Aktions-Thread ausführen und darauf warten — für eine
        Hotkey-Schleife, die (wie unter Linux) streng nacheinander arbeitet.
        Das Warten läuft in kurzen Schritten: ein Signal-Handler (Not-Aus)
        kommt dazwischen, und nach abandon() kehrt der Aufruf sofort zurück.
        on_stall wird einmal gerufen, wenn fn länger als stall_after läuft.
        Rückgabe: True = fertig, False = zurückgelassen."""
        done = threading.Event()

        def wrapped():
            try:
                fn()
            finally:
                done.set()
        gen = self._gen
        self.submit(name, wrapped)
        start = time.monotonic()
        warned = False
        while not done.wait(poll):
            if self._gen != gen:
                return False
            if not warned and time.monotonic() - start > stall_after:
                warned = True
                if on_stall is not None:
                    on_stall(time.monotonic() - start)
        return True

    def stop(self):
        self._stopped.set()


class ChordPump(threading.Thread):
    """Ereignis-Thread für Plattformen, deren Hotkey-Quelle nur eine Queue mit
    (Taste, gedrückt) liefert (Windows Raw Input). Er treibt die ChordMachine
    und führt den SYNCHRONEN Teil der Aktionen selbst aus — on_start, und von
    finish/cancel alles, was die Aufnahme betrifft. Gibt finish() einen Rest
    zurück, geht er an den ActionRunner. Damit liegt das Beenden einer
    Aufnahme immer vor dem Start der nächsten, wie im macOS-Listener.

    Andere Threads (Qt-Oberfläche) greifen nie direkt auf Maschine oder
    Recorder zu, sondern reichen Aufträge über post() herein."""

    def __init__(self, events, core, runner, group_a, group_b,
                 hold_min=0.5, double_window=0.45, is_enabled=None, log=None):
        super().__init__(daemon=True, name="dictation-events")
        self.events = events
        self.core = core
        self.runner = runner
        self.is_enabled = is_enabled or (lambda: True)
        self._log = log or (lambda msg: None)
        self._calls = queue.Queue()
        self._lock = threading.Lock()
        self._pending = []
        self._stop = threading.Event()
        self.last_event_tick = time.monotonic()
        self.current_sync = None
        self.machine = self._make_machine(group_a, group_b, hold_min, double_window)

    def _make_machine(self, group_a, group_b, hold_min, double_window):
        core = self.core
        # on_start bleibt synchron: sein Rückgabewert entscheidet, ob die
        # Maschine armiert (fehlgeschlagener Start darf nicht in 'hold' führen).
        machine = ChordMachine(
            group_a, group_b, core.start_recording,
            lambda: self._pending.append(("finish", core.finish)),
            lambda r: self._pending.append(("cancel", lambda: core.cancel_recording(r))),
            hold_min=hold_min, double_window=double_window)
        machine.on_handsfree = lambda: self._pending.append(
            ("handsfree", core.enable_streaming))
        return machine

    def run(self):
        while not self._stop.is_set():
            self.step()

    def step(self, timeout=PUMP_POLL, now=None):
        """Eine Runde: Aufträge, höchstens ein Tastenereignis, Poll-Tick."""
        self.last_event_tick = time.monotonic()
        while True:
            try:
                call = self._calls.get_nowait()
            except queue.Empty:
                break
            self._run_sync("call", call)
        try:
            item = self.events.get(timeout=timeout) if timeout else self.events.get_nowait()
        except queue.Empty:
            item = None
        with self._lock:
            if item is not None and self.is_enabled():
                vk, pressed = item
                self.machine.key(vk, pressed, now)
            self.machine.poll(now)
            pending, self._pending = self._pending, []
        for name, cb in pending:
            rest = self._run_sync(name, cb)
            if callable(rest):
                self.runner.submit(name, rest)
        self.last_event_tick = time.monotonic()

    def _run_sync(self, name, cb):
        self.current_sync = name
        try:
            return cb()
        except Exception as exc:   # noqa: BLE001 — dieser Thread darf nie sterben
            self._log("events: %r fehlgeschlagen: %r" % (name, exc))
            return None
        finally:
            self.current_sync = None

    def post(self, fn):
        """fn auf dem Ereignis-Thread ausführen (Reihenfolge wie Tasten)."""
        self._calls.put(fn)

    def reconfigure(self, group_a, group_b, hold_min, double_window):
        """Hotkey live wechseln — nur im Ruhezustand, sonst verlöre die
        Maschine ihren Zustand. Läuft auf dem Ereignis-Thread."""
        def apply():
            m = self.machine
            m.hold_min, m.double_window = hold_min, double_window
            if (m.a, m.b) != (set(group_a), set(group_b)) and m.state == "idle" \
                    and not m.pending_finish and not self.core.rec.active:
                m.a, m.b = set(group_a), set(group_b)
                m.pressed.clear()
        self.post(apply)

    def force_reset(self):
        """Maschine hart in den Ruhezustand zwingen (nach dem Not-Aus). Aus
        jedem Thread; wartet höchstens kurz auf den Lock."""
        if not self._lock.acquire(timeout=1.0):
            self._log("events: Lock blockiert -> Maschine nicht zurückgesetzt")
            return
        try:
            self.machine.state = "idle"
            self.machine.pressed.clear()
            self.machine.pending_finish = False
            self._pending = []
        finally:
            self._lock.release()

    def stop(self):
        self._stop.set()
