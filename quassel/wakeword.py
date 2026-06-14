"""Wake-Word-Freisprechen (opt-in): lokal auf eine Phrase hoeren, dann diktieren.

Wenn in der Konfiguration aktiviert, lauscht Quassel mit dem lokalen Whisper-
Modell auf eine frei waehlbare Aufweck-Phrase (Standard "Hey Quassel"). Wird die
Phrase am Anfang einer Aeusserung erkannt, diktiert Quassel den Rest -- entweder
gleich den nachgestellten Text ("Hey Quassel, schreib das hier") oder, wenn nur
die Phrase gesagt wurde, die naechste Aeusserung.

Damit dieses Modul rein und testbar bleibt, werden alle Audio-, Transkriptions-
und Einfuege-Operationen als Callables INJIZIERT. Der Daemon reicht spaeter die
echten Implementierungen herein; Tests nutzen einfache Fakes -- ohne echtes
Audio, echte Threads oder Sleeps.

Es wird bewusst nur die Standardbibliothek benutzt (re/unicodedata/threading).
"""
import re
import threading
import unicodedata

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


def match_wake(transcript, phrase):
    """Prueft, ob ``transcript`` mit ``phrase`` (als ganze Woerter) beginnt.

    Rueckgabe ``(matched, remainder)``:
      * ``matched`` ist True, wenn der normalisierte Transkript-Text mit der
        normalisierten Phrase als vollstaendige Wortfolge beginnt. "hey quassel"
        passt also auf "Hey Quassel, schreib das", aber NICHT auf "heyquasselfoo".
      * ``remainder`` ist der nachgestellte Teil des URSPRUENGLICHEN Transkripts
        nach den Phrasen-Woertern, von fuehrenden Satzzeichen/Leerraum befreit.
        Ist der Transkript nur die Phrase, ist ``remainder`` == "".
      * Bei keinem Treffer: ``(False, "")``.

    Tolerant gegenueber zusaetzlichen Leerzeichen, nachgestellten Satzzeichen
    nach der Phrase und Gross-/Kleinschreibung.
    """
    norm_phrase = normalize(phrase)
    norm_tr = normalize(transcript)
    if not norm_phrase or not norm_tr:
        return (False, "")

    phrase_words = norm_phrase.split(" ")
    tr_words = norm_tr.split(" ")
    n = len(phrase_words)
    # Phrase muss am Anfang als vollstaendige Wortfolge stehen.
    if tr_words[:n] != phrase_words:
        return (False, "")

    # Treffer. Den Rest aus dem ORIGINAL-Transkript schneiden, damit die echte
    # Schreibweise/Interpunktion des Diktats erhalten bleibt. Dafuer ueber die
    # Original-Woerter laufen und nach dem n-ten Wort abschneiden.
    remainder = _original_remainder(transcript, n)
    return (True, remainder)


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


class WakeListener(threading.Thread):
    """Hoert (ueber injizierte Callables) auf das Wake-Word und diktiert dann.

    Injizierte Callables:
      record_utterance() -> bytes|None : nimmt EINE Aeusserung auf (blockiert,
          nutzt VAD); b"" / None bedeutet "nichts aufgenommen".
      transcribe(pcm: bytes) -> str|None : Aeusserung zu Text.
      insert(text: str) -> None : fertigen Text einfuegen (Daemon: refine +
          paste + Statistik).
      is_busy() -> bool : True, waehrend ein Tastatur-Diktat laeuft; dann
          pausiert der Listener.

    ``cfg`` ist ein Objekt mit ``.wakeword_enabled`` (bool) und
    ``.wakeword_phrase`` (str), die live (pro Zyklus) gelesen werden.

    Fuer Pausen in der Schleife wird ``self._stop.wait(...)`` benutzt, niemals
    ``time.sleep`` -- so laesst der Thread sich sofort stoppen.
    """

    def __init__(self, cfg, record_utterance, transcribe, insert, is_busy=None,
                 idle_sec=0.3):
        super().__init__(daemon=True)
        self.cfg = cfg
        self._record = record_utterance
        self._transcribe = transcribe
        self._insert = insert
        self._is_busy = is_busy
        self.idle_sec = idle_sec
        self._stop = threading.Event()

    # -- defensive Wrapper: ein Fehler im injizierten Callable darf den Listener
    #    nie aus der Bahn werfen.
    def _safe_record(self):
        try:
            return self._record()
        except Exception:
            return None

    def _safe_transcribe(self, pcm):
        try:
            return self._transcribe(pcm)
        except Exception:
            return None

    def _safe_insert(self, text):
        try:
            self._insert(text)
        except Exception:
            pass

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
        if not matched:
            return None

        if rem.strip():
            # Phrase + nachgestellter Text in einer Aeusserung: gleich einfuegen.
            self._safe_insert(rem)
            return rem

        # Nur die Phrase gesagt -> die naechste Aeusserung als Diktat aufnehmen.
        pcm2 = self._safe_record()
        if not pcm2:
            return None
        t2 = self._safe_transcribe(pcm2)
        if t2 and t2.strip():
            self._safe_insert(t2)
            return t2
        return None

    def run(self):
        """Daemon-Schleife: zyklisch lauschen, dazwischen kurz idlen (stoppbar)."""
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.idle_sec)

    def stop(self):
        """Setzt das Stop-Ereignis; die Schleife verlaesst run() beim naechsten Mal."""
        self._stop.set()
