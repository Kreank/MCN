"""Etagen-Sortierung der Gebäudeansicht — reine Funktion, ohne Datenbank.

Die Etage ist Freitext (`property.unit.storey`, 0124). Damit ein Haus gezeichnet
werden kann, braucht es eine Reihenfolge; die wird abgeleitet, nie gespeichert.
Diese Tests halten zwei Zusagen fest:

1. Was gedeutet wird, sitzt in der richtigen Höhe (Dach über OG über EG über
   Keller).
2. Was sich **nicht** deuten lässt, liefert `None` — und wird nicht geraten.
   Ein Monteur, der wegen einer geratenen Etage im falschen Stockwerk klingelt,
   ist der Schaden, den diese Regel verhindert.
"""
import pytest

from db_core.services.gebaeudeansicht import etagen_ordnung


@pytest.mark.parametrize(
    "text,erwartet",
    [
        # Erdgeschoss und seine Schreibweisen
        ("EG", 0.0),
        ("eg", 0.0),
        ("Erdgeschoss", 0.0),
        ("Erdgeschoß", 0.0),
        ("Parterre", 0.0),
        ("0", 0.0),
        # Obergeschosse
        ("1. OG", 1.0),
        ("1.OG", 1.0),
        ("1 OG", 1.0),
        ("OG 2", 2.0),
        ("3. Obergeschoss", 3.0),
        ("4. Etage", 4.0),
        ("5. Stock", 5.0),
        ("12", 12.0),
        # Zwischenlagen
        ("Hochparterre", 0.5),
        ("Zwischengeschoss", 0.5),
        # Unter der Erde
        ("KG", -1.0),
        ("Keller", -1.0),
        ("UG", -1.0),
        ("Souterrain", -0.5),
        ("2. UG", -2.0),
        ("Tiefgarage", -2.0),
        # Ganz oben
        ("DG", 800.0),
        ("Dachgeschoss", 800.0),
        ("Spitzboden", 900.0),
    ],
)
def test_bekannte_etagen_werden_gedeutet(text, erwartet):
    assert etagen_ordnung(text) == erwartet


@pytest.mark.parametrize("text", [None, "", "   ", "Gartenhaus", "links hinten", "A"])
def test_undeutbares_wird_nicht_geraten(text):
    """`None` heißt „nicht deutbar" — die Ansicht zeigt es in einem eigenen Band."""
    assert etagen_ordnung(text) is None


def test_reihenfolge_stimmt_von_oben_nach_unten():
    """Die abgeleiteten Werte müssen sich wie ein Haus sortieren lassen."""
    etagen = ["2. UG", "KG", "EG", "Hochparterre", "1. OG", "2. OG", "DG"]
    sortiert = sorted(etagen, key=lambda s: -etagen_ordnung(s))
    assert sortiert == ["DG", "2. OG", "1. OG", "Hochparterre", "EG", "KG", "2. UG"]
