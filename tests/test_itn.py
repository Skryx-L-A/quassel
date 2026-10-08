"""ITN is deliberately limited to clear spoken forms."""
import pytest

from quassel.itn import normalize


@pytest.mark.parametrize("raw,expected", [
    ("Ich habe drei Katzen.", "Ich habe 3 Katzen."),
    ("twenty-five pages", "25 pages"),
    ("einundzwanzig Seiten", "21 Seiten"),
    ("fünf Prozent", "5 %"),
    ("twenty percent", "20 %"),
    ("drei Uhr dreißig", "3:30 Uhr"),
    ("three o'clock", "3:00"),
    ("fünf Euro", "5 €"),
    ("five dollars", "$5"),
    ("fünfter Mai", "5. Mai"),
    ("May fifth", "May 5"),
    ("eins nach dem anderen", "eins nach dem anderen"),
    ("one of us", "one of us"),
    ("mail@example.com und https://example.com/five", "mail@example.com und https://example.com/five"),
])
def test_itn(raw, expected):
    assert normalize(raw) == expected
