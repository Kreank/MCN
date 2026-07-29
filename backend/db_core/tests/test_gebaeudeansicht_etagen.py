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

from db_core.services.gebaeudeansicht import (
    _natuerlich,
    etage_deuten,
    etagen_ordnung,
    lage_aus_text,
)


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


# --------------------------------------------------------------------------
# Die Lage steht mit im Etagenfeld — „EG links" ist der Normalfall, nicht der
# Ausreißer. Ohne Abspalten wird daraus ein eigenes Band, und weil dann nichts
# mehr deutbar ist, steht das EG über dem 3. OG.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,ordnung,label,lage",
    [
        ("EG links", 0.0, "EG", "links"),
        ("EG rechts", 0.0, "EG", "rechts"),
        ("EG Mitte", 0.0, "EG", "Mitte"),
        ("EG Li", 0.0, "EG", "links"),
        ("EG re", 0.0, "EG", "rechts"),
        ("3. OG Rechts", 3.0, "3. OG", "rechts"),
        ("2.OG links", 2.0, "2.OG", "links"),
        ("Dachgeschoss rechts", 800.0, "Dachgeschoss", "rechts"),
        # Ohne Zusatz bleibt alles wie vorher.
        ("1. OG", 1.0, "1. OG", None),
        ("EG", 0.0, "EG", None),
    ],
)
def test_lage_wird_abgespalten(text, ordnung, label, lage):
    o, l, lg = etage_deuten(text)
    assert (o, l, lg.anzeige if lg else None) == (ordnung, label, lage)


@pytest.mark.parametrize("text", ["links hinten", "Gartenebene links", "links"])
def test_ohne_deutbare_etage_wird_nichts_zerlegt(text):
    """Was wir nicht verstanden haben, nehmen wir auch nicht auseinander.

    Der Text bleibt ganz und landet unten im Ungedeutet-Band — statt dass aus
    „links hinten" die Etage „hinten" wird.
    """
    ordnung, label, lage = etage_deuten(text)
    assert (ordnung, label, lage) == (None, text, None)


def test_nackte_zahl_gilt_nur_im_etagenfeld():
    """„3" ist im Feld Etage das 3. OG — in der Nummer die **Wohnung 3**."""
    assert etage_deuten("3")[0] == 3.0
    assert etage_deuten("3", nur_mit_wort=True)[0] is None
    assert etage_deuten("WE 3", nur_mit_wort=True)[0] is None
    # Ein echtes Etagenwort in der Nummer darf aushelfen.
    o, label, lage = etage_deuten("EG links", nur_mit_wort=True)
    assert (o, label, lage.anzeige) == (0.0, "EG", "links")


@pytest.mark.parametrize(
    "nummer,erwartet",
    [
        ("Laden links", "links"),
        ("WE 3 re", "rechts"),
        ("WE 01", None),
        # Wortweise gesucht: „Remise" ist kein „re".
        ("Remise", None),
        ("Mittelbau", None),
    ],
)
def test_lage_aus_der_einheitennummer(nummer, erwartet):
    gefunden = lage_aus_text(nummer)
    assert (gefunden[1] if gefunden else None) == erwartet


def test_nummern_sortieren_natuerlich():
    """„WE 10" hinter „WE 2" — Textsortierung stellt es davor."""
    nummern = ["WE 10", "WE 2", "WE 1", "WE 21", "S 3"]
    assert sorted(nummern, key=_natuerlich) == ["S 3", "WE 1", "WE 2", "WE 10", "WE 21"]
