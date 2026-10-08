"""Wake-Word-Freisprechen (opt-in): lokal auf eine Phrase hoeren, dann diktieren.

Wenn in der Konfiguration aktiviert, lauscht Quassel mit dem lokalen Whisper-
Modell auf eine frei waehlbare Aufweck-Phrase (Standard "Hey Quassel"). Wird die
Phrase am Anfang einer Aeusserung erkannt, diktiert Quassel den Rest -- entweder
gleich den nachgestellten Text ("Hey Quassel, schreib das hier") oder, wenn nur
die Phrase gesagt wurde, die naechste Aeusserung.

Optional steht VOR der Whisper-Pruefung ein Schluesselwort-Erkenner (Keyword-
Spotter, openWakeWord-ONNX): er hoert dauernd mit, ist winzig und schaltet erst
bei einem Treffer die Aufnahme und Whisper frei. Ohne Modell oder ohne
onnxruntime bleibt es beim bisherigen Verfahren (jede Aeusserung zu Whisper).

Damit dieses Modul rein und testbar bleibt, werden alle Audio-, Transkriptions-
und Einfuege-Operationen als Callables INJIZIERT. Der Daemon reicht spaeter die
echten Implementierungen herein; Tests nutzen einfache Fakes -- ohne echtes
Audio, echte Threads oder Sleeps.

Es wird bewusst nur die Standardbibliothek benutzt (re/unicodedata/threading);
numpy und onnxruntime kommen nur beim Laden eines Spotter-Modells ins Spiel.
"""
import contextlib
import difflib
import os
import re
import threading
import time
import unicodedata

from . import vad

# Aehnlichkeitsschwelle fuer den unscharfen Phrasen-Abgleich. Whisper verhoert
# sich beim Kunstwort "Quassel" oft ("Hey Kassel", "Hey Castle", ...); ein
# strikter Wort-fuer-Wort-Vergleich wuerde das Wake-Word dann nie ausloesen.
WAKE_FUZZY = 0.72

# Erlaubte Zeichen in normalisiertem Text: Buchstaben (inkl. aeoeuess), Ziffern,
# Leerzeichen. Alles andere (Satzzeichen) wird zu Leerzeichen.
_KEEP = re.compile(r"[^0-9a-zäöüß\s]+", re.UNICODE)
_WS = re.compile(r"\s+", re.UNICODE)


def normalize(text):
    """Kleinschreiben, Satzzeichen entfernen, Leerraum zusammenfassen, trimmen.

    Behaelt Buchstaben (inkl. ae/oe/ue/ss bzw. ä/ö/ü/ß), Ziffern und einfache
    Leerzeichen. Gibt fuer None/Leereingabe "" zurueck.
    """
    if not text:
        return ""
    # lower() (nicht casefold), damit "ß" als "ß" erhalten bleibt. Vorher NFC
    # normalisieren, damit kombinierte Umlaute als ein Zeichen vorliegen.
    t = unicodedata.normalize("NFC", text).lower()
    t = _KEEP.sub(" ", t)
    t = _WS.sub(" ", t)
    return t.strip()


def _similar(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


# Lautform fuer den Vergleich: Whisper schreibt das Kunstwort, wie es klingt
# ("Quassel" -> "Kassel", "Castle", "Kwassel", "Quasel"). Beide Seiten werden auf
# eine grobe deutsch/englische Lautschrift gebracht, damit solche Schreibweisen
# naeher beieinander liegen als ihre Buchstaben.
_SOUND_SUBS = (
    ("ß", "ss"), ("ä", "e"), ("ö", "o"), ("ü", "u"), ("sch", "s"), ("ph", "f"),
    ("qu", "kw"), ("q", "k"), ("ck", "k"), ("ch", "k"), ("c", "k"), ("v", "f"),
    ("z", "ts"), ("x", "ks"), ("ey", "ai"), ("ay", "ai"), ("ei", "ai"),
    ("ai", "ai"), ("y", "i"), ("w", "v"),
)
_DOUBLES = re.compile(r"(.)\1+")


def _sound(text):
    t = text
    for a, b in _SOUND_SUBS:
        t = t.replace(a, b)
    return _DOUBLES.sub(r"\1", t)


def _score(heard, phrase):
    """Aehnlichkeit (0..1) zweier normalisierter Texte.

    Gleich viele Woerter: das Beste aus Schreibweise und Lautform, aber das
    kennzeichnende (laengste) Phrasenwort muss fuer sich noch passen -- ein
    kurzes, genau getroffenes Wort ("hey") darf kein anderes tragen.
    Anderes Wortzahl (zusammengezogen "heykassel" fuer "hey kassel", oder
    auseinandergezogen): Lautform ohne Leerzeichen, nur bei gleicher Laenge +-1,
    sonst wuerde "heyquasselfoo" die Phrase "hey quassel" treffen."""
    hw, pw = heard.split(" "), phrase.split(" ")
    snd_h, snd_p = _sound(heard), _sound(phrase)
    if len(hw) == len(pw):
        key = max(range(len(pw)), key=lambda i: len(pw[i]))
        if _similar(_sound(hw[key]), _sound(pw[key])) < 0.6:
            return 0.0
        return max(_similar(heard, phrase), _similar(snd_h, snd_p))
    jh, jp = snd_h.replace(" ", ""), snd_p.replace(" ", "")
    if abs(len(jh) - len(jp)) <= 1:
        return _similar(jh, jp)
    return 0.0


# Vorsilben, die Whisper vor die Phrase setzt, ohne dass der Sprecher sie meinte.
_FILLERS = frozenset(("ok", "okay", "ja", "äh", "ähm", "also", "und", "so", "hm",
                      "hmm", "eh", "uh", "um", "na", "oh", "naja"))


def match_wake(transcript, phrase, fuzzy=WAKE_FUZZY):
    """Prueft, ob ``transcript`` mit ``phrase`` beginnt (exakt ODER aehnlich).

    Rueckgabe ``(matched, remainder)``:
      * ``matched`` ist True, wenn die ersten Woerter des Transkripts die Phrase
        sind -- exakt als Wortfolge oder unscharf (Aehnlichkeit >= ``fuzzy``),
        damit Whisper-Verhoerer des Kunstworts ("Hey Kassel", "Hey Castle",
        "Heykassel") trotzdem ausloesen. Verglichen wird auch in Lautform; ein
        einzelnes Fuellwort davor ("Ok, hey Quassel") ist erlaubt. "hey quassel"
        passt auf "Hey Quassel, schreib das", aber nicht auf "heyquasselfoo".
        Sehr kurze Phrasen (bis 6 Zeichen) verlangen mindestens 0,9.
      * ``remainder`` ist der nachgestellte Teil des URSPRUENGLICHEN Transkripts
        nach den Phrasen-Woertern, von fuehrenden Satzzeichen/Leerraum befreit.
        Ist der Transkript nur die Phrase, ist ``remainder`` == "".
      * Bei keinem Treffer: ``(False, "")``.

    Tolerant gegenueber Leerzeichen, nachgestellten Satzzeichen und Gross-/
    Kleinschreibung.
    """
    norm_phrase = normalize(phrase)
    norm_tr = normalize(transcript)
    if not norm_phrase or not norm_tr:
        return (False, "")
    if len(norm_phrase) <= 6:
        fuzzy = max(fuzzy, 0.9)

    n = len(norm_phrase.split(" "))
    tr_words = norm_tr.split(" ")
    offsets = (0, 1) if tr_words[0] in _FILLERS and len(tr_words) > 1 else (0,)

    # Kandidaten: so viele Woerter wie die Phrase, eines weniger (zusammengezogen)
    # oder eines mehr (auseinandergezogen); Gleichstand gewinnt der frueheste.
    best, best_words = 0.0, None
    for off in offsets:
        for k in (n, n - 1, n + 1):
            if k < 1 or off + k > len(tr_words):
                continue
            head = " ".join(tr_words[off:off + k])
            score = 1.0 if head == norm_phrase else _score(head, norm_phrase)
            if score > best:
                best, best_words = score, off + k
    if best_words is None and len(tr_words) < n:
        # Kuerzer als die Phrase: ganzer Transkript vs. Phrase unscharf.
        if _score(norm_tr, norm_phrase) >= fuzzy:
            return (True, "")
        return (False, "")
    if best_words is not None and best >= fuzzy:
        # Treffer. Rest aus dem ORIGINAL-Transkript schneiden, damit echte
        # Schreibweise/Interpunktion erhalten bleibt.
        return (True, _original_remainder(transcript, best_words))
    return (False, "")


# Wort = zusammenhaengende Folge erlaubter Zeichen (Buchstaben/Ziffern).
_WORDISH = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", re.UNICODE)


def _original_remainder(transcript, skip_words):
    """Liefert den Originaltext nach den ersten ``skip_words`` Woertern.

    Fuehrende Satzzeichen/Leerzeichen des Rests werden entfernt. Sind nach den
    uebersprungenen Woertern keine weiteren Woerter mehr da, ist das Ergebnis "".
    """
    if not transcript:
        return ""
    matches = list(_WORDISH.finditer(transcript))
    if len(matches) <= skip_words:
        return ""
    # Ab dem Ende des letzten Phrasen-Wortes weiterschneiden.
    start = matches[skip_words - 1].end()
    rest = transcript[start:]
    # Fuehrende Nicht-Wort-Zeichen (Komma, Leerzeichen, ...) abstreifen.
    return rest.lstrip(" \t\r\n,.;:!?-–— \"'`")


# ------------------------------------------------------ Einstellungen (config.ini)
class WakeSettings:
    """Wake-Word-Einstellungen jenseits von Schalter und Phrase. Quelle:
    Attribute der Cfg (wakeword_spotter_model, ...), sonst [wakeword] der
    config.ini:

        [wakeword]
        spotter_model =            # Pfad zum Keyword-ONNX (openWakeWord); leer = aus
        spotter_threshold = 0.5    # Ausloeseschwelle 0.05 .. 0.99
        verify = true              # Whisper prueft die Phrase nach dem Spotter noch
    """

    def __init__(self, spotter_model="", spotter_threshold=0.5, verify=True):
        self.spotter_model = spotter_model
        self.spotter_threshold = spotter_threshold
        self.verify = verify


def wake_settings(cfg=None):
    ini = None

    def pick(attr, key, default):
        nonlocal ini
        value = getattr(cfg, attr, None) if cfg is not None else None
        if value is not None:
            return value
        if ini is None:
            ini = vad.read_ini()
        return ini.get("wakeword", key, fallback=default)

    return WakeSettings(
        spotter_model=str(pick("wakeword_spotter_model", "spotter_model", "")).strip(),
        spotter_threshold=vad.as_float(
            pick("wakeword_spotter_threshold", "spotter_threshold", 0.5), 0.5, 0.05, 0.99),
        verify=vad.as_bool(pick("wakeword_verify", "verify", True), True))


# ------------------------------------------------------ Schluesselwort-Erkenner
SPOTTER_CHUNK = 1280        # Samples je Schritt (80 ms bei 16 kHz)
_SPOTTER_CONTEXT = 480      # Samples Vorlauf fuer das Mel-Spektrogramm (3 Hops)
_MEL_WINDOW = 76            # Mel-Zeilen je Einbettung
_FEATURES = 16              # Einbettungen je Entscheidung
MEL_FILE = "melspectrogram.onnx"
EMBED_FILE = "embedding_model.onnx"


class OpenWakeWordSpotter:
    """Schluesselwort-Erkenner im Format von openWakeWord: drei ONNX-Modelle
    hintereinander (Mel-Spektrogramm, Einbettung, Schluesselwort). Schnittstelle
    eines jeden Spotters: ``chunk_samples``, ``feed(chunk) -> bool`` (True bei
    Treffer), ``reset()``. Quassel liefert keine Modelle mit; ein deutsches
    Wort muss man selbst trainieren und den Pfad eintragen.

    Der Ablauf folgt openWakeWord 0.6 (Streaming): je 1280-Sample-Schritt kommen
    ca. 8 Mel-Zeilen dazu, aus den letzten 76 wird eine 96-dimensionale Einbettung,
    aus den letzten 16 Einbettungen der Wert 0..1. Nur mit Attrappen geprueft,
    nicht gegen echte Modelldateien -- siehe die Hinweise im Ergebnis.
    """

    chunk_samples = SPOTTER_CHUNK

    def __init__(self, np, melspec, embedding, model, threshold=0.5, refractory_chunks=25):
        self._np = np
        self._mel, self._emb, self._model = melspec, embedding, model
        self.threshold = threshold
        self.refractory_chunks = refractory_chunks
        self.last_score = 0.0
        self.reset()

    def reset(self):
        np = self._np
        self._raw = np.zeros(SPOTTER_CHUNK + _SPOTTER_CONTEXT, dtype=np.float32)
        self._mel_rows = []
        self._embeddings = []
        self._cooldown = 0
        self.last_score = 0.0

    @staticmethod
    def _run(session, array):
        return session.run(None, {session.get_inputs()[0].name: array})[0]

    def feed(self, chunk):
        np = self._np
        if len(chunk) != SPOTTER_CHUNK * 2:
            raise ValueError("Spotter braucht Schritte zu %d Samples" % SPOTTER_CHUNK)
        x = np.frombuffer(chunk, dtype="<i2").astype(np.float32)
        self._raw = np.concatenate([self._raw, x])[-(SPOTTER_CHUNK + _SPOTTER_CONTEXT):]
        spec = np.asarray(self._run(self._mel, self._raw[None, :]), dtype=np.float32)
        spec = spec.reshape(-1, spec.shape[-1]) / 10.0 + 2.0       # openWakeWord-Skalierung
        self._mel_rows = (self._mel_rows + list(spec))[-4 * _MEL_WINDOW:]
        if len(self._mel_rows) < _MEL_WINDOW:
            return False
        window = np.stack(self._mel_rows[-_MEL_WINDOW:])[None, :, :, None]
        emb = np.asarray(self._run(self._emb, window), dtype=np.float32).reshape(-1)
        self._embeddings = (self._embeddings + [emb])[-_FEATURES:]
        if len(self._embeddings) < _FEATURES:
            return False
        out = self._run(self._model, np.stack(self._embeddings)[None, :, :])
        self.last_score = float(np.asarray(out).reshape(-1)[0])
        if self._cooldown > 0:
            self._cooldown -= 1
            return False
        if self.last_score >= self.threshold:
            self._cooldown = self.refractory_chunks
            return True
        return False


def _find_model(name, dirs):
    for d in dirs:
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return None


def load_spotter(cfg=None, log=None, runtime=None):
    """Spotter nach Einstellung oder None (= bisheriges Verfahren). None heisst:
    kein Modell eingetragen, Datei oder onnxruntime fehlen, die Hilfsmodelle
    (melspectrogram.onnx, embedding_model.onnx im Ordner des Modells oder unter
    Modellordner/wakeword) fehlen, oder das Laden schlug fehl. Es wird nie
    etwas heruntergeladen. ``runtime`` = (numpy, onnxruntime) fuer Tests."""
    s = wake_settings(cfg)
    if not s.spotter_model:
        return None
    path = os.path.expanduser(s.spotter_model)

    def say(msg):
        if log:
            log("wake: " + msg)

    if not os.path.isfile(path):
        say("Spotter-Modell nicht gefunden -> Whisper-Verfahren")
        return None
    from . import vad_silero
    np, ort = runtime if runtime is not None else vad_silero.import_runtime()
    if ort is None:
        say("onnxruntime fehlt -> Whisper-Verfahren")
        return None
    try:
        from . import config
        dirs = [os.path.dirname(path), os.path.join(config.MODELDIR, "wakeword")]
        mel, emb = _find_model(MEL_FILE, dirs), _find_model(EMBED_FILE, dirs)
        if mel is None or emb is None:
            say("%s/%s fehlen neben dem Spotter-Modell -> Whisper-Verfahren"
                % (MEL_FILE, EMBED_FILE))
            return None
        return OpenWakeWordSpotter(
            np, vad_silero.load_session(mel, ort), vad_silero.load_session(emb, ort),
            vad_silero.load_session(path, ort), threshold=s.spotter_threshold)
    except Exception as exc:    # noqa: BLE001 -- kaputtes Modell: bisheriges Verfahren
        say("Spotter nicht ladbar (%s) -> Whisper-Verfahren" % type(exc).__name__)
        return None


# ------------------------------------------------------------- Aufnahme einer Aeusserung
def capture_utterance(rec, mic, classifier, spotter=None, log=None, window=12.0,
                      poll=0.1, preroll_sec=2.0, min_bytes=8000, active=None,
                      sleep=time.sleep, clock=time.monotonic):
    """Eine Aeusserung aus ``rec`` aufnehmen (Recorder mit start/stop/raw_bytes).

    Ohne ``spotter``: bis zu ``window`` Sekunden aufnehmen und nach Stille
    beenden (vad.SilenceDetector mit ``classifier``), wie bisher. Mit
    ``spotter``: erst dauernd lauschen, bis er anschlaegt -- Whisper laeuft bis
    dahin nie --, dann die letzten ``preroll_sec`` Sekunden (darin steht die
    Phrase) plus alles Folgende bis zur Stille aufnehmen.

    Rueckgabe: PCM-Bytes oder None (keine Sprache, kein Treffer, zu kurz). Die
    Rohdatei raeumt der Aufrufer ab. Geloggt werden nur Zahlen, nie Inhalt."""
    if not rec.start(mic):
        return None
    detector = vad.SilenceDetector(classifier=classifier, min_speech_sec=0.25,
                                   hang_sec=1.0)
    if spotter is not None:
        spotter.reset()
    chunk_bytes = (spotter.chunk_samples * 2) if spotter is not None else 0
    seen = 0            # bis hierher ist die Aufnahme verarbeitet
    spotted = 0         # bis hierher hat der Spotter Schritte bekommen
    start = 0           # ab hier gehoert die Aeusserung zum Ergebnis
    fired = spotter is None
    deadline = clock() + window
    try:
        while clock() < deadline and (active is None or active()):
            sleep(poll)
            data = rec.raw_bytes()
            if len(data) <= seen:
                continue
            if not fired:
                while spotted + chunk_bytes <= len(data) and not fired:
                    fired = spotter.feed(data[spotted:spotted + chunk_bytes])
                    spotted += chunk_bytes
                if fired:
                    start = max(0, spotted - int(preroll_sec * 16000) * 2)
                    detector.feed(data[start:])
                    seen = len(data)
                    # Nach dem Treffer bleibt Zeit fuer den Satz dahinter.
                    deadline = max(deadline, clock() + 12.0)
                else:
                    seen = len(data)
                continue
            detector.feed(data[seen:])
            seen = len(data)
            if detector.stopped:
                break
    finally:
        rec.stop()
    data = rec.raw_bytes()[start:]
    if log:
        log("wake: rec %d bytes, spotter=%s, fired=%s, speech=%s" % (
            len(data), spotter is not None, fired, detector.speech_started))
    if not fired or len(data) < min_bytes:
        return None
    if spotter is None and not detector.speech_started:
        return None        # der Spotter bezeugt die Sprache selbst, die Energieschwelle nicht
    return data


# ------------------------------------------------------ Sperre zwischen den Einfuegern
class InsertGate:
    """Verhindert, dass das Einfuegen eines Wake-Diktats und das eines Tastatur-
    Diktats gleichzeitig laufen (Zwischenablage, Tastenanschlaege; Audit G97).

    Die Wake-Seite prueft ``is_busy`` und besetzt die Sperre in einem Schritt
    (enter_wake): ist ein Tastatur-Diktat im Gang oder wird gerade eingefuegt,
    wird das Wake-Diktat verworfen, nicht zwischengeschoben. Die Tastatur-Seite
    (dictation) wartet hoechstens ``timeout`` Sekunden auf ein laufendes
    Wake-Einfuegen und geht dann trotzdem weiter -- sie blockiert nie dauerhaft.
    Der Thread, der die Wake-Sperre haelt, darf selbst durch dictation() hindurch."""

    def __init__(self):
        self._cv = threading.Condition()
        self._wake_thread = None
        self._dictations = 0

    def enter_wake(self, is_busy=None):
        with self._cv:
            if self._wake_thread is not None or self._dictations > 0:
                return False
            if is_busy is not None and is_busy():
                return False
            self._wake_thread = threading.get_ident()
            return True

    def exit_wake(self):
        with self._cv:
            if self._wake_thread == threading.get_ident():
                self._wake_thread = None
                self._cv.notify_all()

    @property
    def wake_inserting(self):
        return self._wake_thread is not None

    @contextlib.contextmanager
    def dictation(self, timeout=5.0):
        """Rahmen um das Einfuegen eines Tastatur-Diktats."""
        with self._cv:
            if self._wake_thread != threading.get_ident():
                end = time.monotonic() + timeout
                while self._wake_thread is not None:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    self._cv.wait(left)
            self._dictations += 1
        try:
            yield
        finally:
            with self._cv:
                self._dictations -= 1


INSERT_GATE = InsertGate()


class WakeListener(threading.Thread):
    """Hoert (ueber injizierte Callables) auf das Wake-Word und diktiert dann.

    Injizierte Callables:
      record_utterance() -> bytes|None : nimmt EINE Aeusserung auf (blockiert,
          nutzt VAD); b"" / None bedeutet "nichts aufgenommen".
      transcribe(pcm: bytes) -> str|None : Aeusserung zu Text.
      insert(text: str) -> None : fertigen Text einfuegen (Daemon: refine +
          paste + Statistik).
      is_busy() -> bool : True, waehrend ein Tastatur-Diktat laeuft; dann
          pausiert der Listener -- und zwar nicht nur vor der Aufnahme, sondern
          noch einmal unmittelbar vor dem Einfuegen, unter der InsertGate-Sperre.
      record_followup() -> bytes|None : (optional) nimmt die Aeusserung NACH der
          reinen Phrase auf, ohne Spotter -- der hat die Phrase ja schon
          gehoert. Fehlt es, gilt record_utterance.

    ``cfg`` ist ein Objekt mit ``.wakeword_enabled`` (bool) und
    ``.wakeword_phrase`` (str), die live (pro Zyklus) gelesen werden.

    Fuer Pausen in der Schleife wird ``self._stop.wait(...)`` benutzt, niemals
    ``time.sleep`` -- so laesst der Thread sich sofort stoppen.
    """

    def __init__(self, cfg, record_utterance, transcribe, insert, is_busy=None,
                 idle_sec=0.3, record_followup=None, gate=None, trust_capture=False):
        super().__init__(daemon=True)
        self.cfg = cfg
        self._record = record_utterance
        self._record_followup = record_followup or record_utterance
        self._transcribe = transcribe
        self._insert = insert
        self._is_busy = is_busy
        self.idle_sec = idle_sec
        self._gate = gate or INSERT_GATE
        # True: die Aufnahme kam hinter einem Spotter, der das Wort gehoert hat;
        # [wakeword] verify = false laesst dann auch Text ohne erkannte Phrase zu.
        self._trust_capture = trust_capture
        self._stop = threading.Event()

    # -- defensive Wrapper: ein Fehler im injizierten Callable darf den Listener
    #    nie aus der Bahn werfen.
    def _safe_record(self, followup=False):
        try:
            return self._record_followup() if followup else self._record()
        except Exception:
            return None

    def _safe_transcribe(self, pcm):
        try:
            return self._transcribe(pcm)
        except Exception:
            return None

    def _safe_insert(self, text):
        """Einfuegen unter der Sperre. False: verworfen, weil inzwischen ein
        Tastatur-Diktat laeuft oder gerade eingefuegt wird (Audit G97) -- ein
        zweiter Einfueger wuerde in dessen Zwischenablage/Tastenfolge greifen."""
        if not self._gate.enter_wake(self._safe_busy):
            return False
        try:
            self._insert(text)
        except Exception:
            pass
        finally:
            self._gate.exit_wake()
        return True

    def _safe_busy(self):
        if self._is_busy is None:
            return False
        try:
            return bool(self._is_busy())
        except Exception:
            # Im Zweifel lieber pausieren als ins laufende Diktat hineinfunken.
            return True

    def run_once(self):
        """Ein vollstaendiger Lausch-Zyklus, ausschliesslich ueber die Callables.

        Wirft nie nach aussen. Rueckgabe ist der diktierte Text bei Erfolg, sonst
        ``None``.
        """
        if not getattr(self.cfg, "wakeword_enabled", False):
            return None
        if self._safe_busy():
            return None

        pcm = self._safe_record()
        if not pcm:
            return None
        text = self._safe_transcribe(pcm)
        if not text or not text.strip():
            return None

        matched, rem = match_wake(text, getattr(self.cfg, "wakeword_phrase", ""))
        if not matched and self._trust_capture and not wake_settings(self.cfg).verify:
            matched, rem = True, text.strip()     # der Spotter hat entschieden
        if not matched:
            return None

        if rem.strip():
            # Phrase + nachgestellter Text in einer Aeusserung: gleich einfuegen.
            return rem if self._safe_insert(rem) else None

        # Nur die Phrase gesagt -> die naechste Aeusserung als Diktat aufnehmen.
        pcm2 = self._safe_record(followup=True)
        if not pcm2:
            return None
        t2 = self._safe_transcribe(pcm2)
        if t2 and t2.strip():
            return t2 if self._safe_insert(t2) else None
        return None

    def run(self):
        """Daemon-Schleife: zyklisch lauschen, dazwischen kurz idlen (stoppbar)."""
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.idle_sec)

    def stop(self):
        """Setzt das Stop-Ereignis; die Schleife verlaesst run() beim naechsten Mal."""
        self._stop.set()
