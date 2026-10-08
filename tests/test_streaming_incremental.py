"""Regression cases for bounded, confirmed-only streaming."""
from quassel.streaming import StreamTyper


class Sink:
    def __init__(self):
        self.text = ""
        self.deleted = []

    def type(self, value):
        self.text += value

    def delete(self, count):
        self.deleted.append(count)
        self.text = self.text[:-count]


def test_rolling_window_keeps_the_69_character_beginning():
    sink = Sink()
    typer = StreamTyper("word", sink.type, sink.delete)
    beginning = "Wir treffen uns morgen um neun und besprechen danach den Plan. Ja!!!!"
    assert len(beginning) == 69
    typer.update(beginning, audio_start=0, audio_end=480000)
    typer.update(beginning, audio_start=0, audio_end=512000)
    assert sink.text == beginning
    typer.update("besprechen danach den Plan. Ja!!!! Und dann geht es weiter",
                 audio_start=96000, audio_end=576000)
    typer.update("besprechen danach den Plan. Ja!!!! Und dann geht es weiter",
                 audio_start=96000, audio_end=608000)
    assert sink.text == beginning + " Und dann geht es weiter"
    assert not sink.deleted
    assert typer.audio_window(800000, 480000) >= 320000


def test_correction_only_confirms_shared_word_prefix():
    sink = Sink()
    typer = StreamTyper("word", sink.type, sink.delete)
    for text in ("Wir fahren nach Berlin", "Wir fahren nach Bern",
                 "Wir fahren nach Bern", "Wir fahren nach Bern morgen"):
        typer.update(text)
    assert sink.text == "Wir fahren nach Bern"
    assert not sink.deleted


def test_repeated_words_and_language_switch_are_not_dropped():
    sink = Sink()
    typer = StreamTyper("stable", sink.type, sink.delete)
    for text in ("Ja ja", "Ja ja", "Ja ja let's", "Ja ja let's go",
                 "Ja ja let's go"):
        typer.update(text)
    assert sink.text == "Ja ja let's go"


def test_silence_error_and_finish_keep_confirmed_text():
    sink = Sink()
    typer = StreamTyper("aggressive", sink.type, sink.delete)
    typer.update("Guten Morgen")
    typer.update("Guten Morgen")
    typer.update("")
    typer.update(None)
    assert sink.text == "Guten Morgen"
    assert typer.finish("Guten Morgen zusammen") == "Guten Morgen zusammen"
    assert not sink.deleted


def test_moving_window_can_confirm_new_tail_on_each_step():
    sink = Sink()
    typer = StreamTyper("word", sink.type, sink.delete)
    typer.update("Wir gehen", audio_start=0, audio_end=320000)
    typer.update("Wir gehen weiter", audio_start=32000, audio_end=352000)
    typer.update("gehen weiter", audio_start=64000, audio_end=384000)
    assert sink.text == "Wir gehen weiter"


def test_audio_window_is_bounded_and_uses_stable_anchor():
    sink = Sink()
    typer = StreamTyper("word", sink.type, sink.delete)
    typer.update("Erster Satz", audio_start=0, audio_end=320000)
    typer.update("Erster Satz", audio_start=0, audio_end=352000)
    assert typer.stable_audio_end == 320000
    start = typer.audio_window(640000, 480000)
    assert start == 288000
    assert 640000 - start <= 480000
