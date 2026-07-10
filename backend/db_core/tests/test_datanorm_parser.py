"""Tests des DATANORM-4-Parsers gegen ECHTE Zeilen aus der Händlerdatei.

Die Beispielsätze stammen wörtlich aus `DATANORM/3STAMM.ZIP` (Artikelstamm) und
`DATANORM/DATANORM (1).ZIP` (Preispflege) von BÄR & OLLENROTH, DATANORM 04, EUR.

Die teuerste Falle steht in `test_preiseinheit_100_stahlhaften`: Listenpreis 12,90
für 100 Stück, minus 40 % Rabatt → 0,0774 € je Stück. Wer die Preiseinheit
ignoriert, legt den Artikel zum Hundertfachen an; wer auf zwei Nachkommastellen
rundet, verliert über drei Prozent.

Keine Datenbank nötig — der Parser ist eine reine Funktion.
"""
from decimal import Decimal

import pytest

from db_core.services import datanorm


# --- Vorlaufsatz ------------------------------------------------------------

def test_vorlaufsatz_version_und_waehrung():
    zeile = (
        "V 020726DATANORM - Datenservice - Artikelstamm  Copyright BÄR & "
        "OLLENROTH KG Berlin     Telefon: 030 / 62 88 1 - 106       04EUR"
    )
    v = datanorm.parse_vorlauf(zeile)
    assert v.datum == "020726"
    assert v.version == "04"
    assert v.waehrung == "EUR"
    assert "OLLENROTH" in v.info


# --- Artikelsatz (A) --------------------------------------------------------

def test_artikel_kanalrohr():
    zeile = (
        "A;N;PPAC12M110300;50;Kanalrohr Acaro SN12 DN100x3000mm;"
        "PP rotbraun mit aufgest.Muffe DIN EN1852;1;0;M;2380;BJBG; ; ;"
    )
    a = datanorm.parse_artikel(zeile)
    assert a.vkz == "N"
    assert a.artikelnummer == "PPAC12M110300"
    assert a.mengeneinheit == "M"
    assert a.preiskennzeichen == 1        # Listenpreis
    assert a.preiseinheit == 0            # je 1
    assert a.listenpreis_cent == 2380
    assert a.rabattgruppe == "BJBG"
    assert a.bezeichnung.startswith("Kanalrohr Acaro SN12 DN100x3000mm")
    # 2380 Cent = 23,80 €, Preiseinheit 0 → je Meter
    assert a.listenpreis == Decimal("23.8000")


def test_artikel_edelstahlrohr_grosser_preis():
    zeile = (
        "A;N;RG6040640U1;00;Edelstahlrohr geschw. 406,4x4,0mm 1.4301;"
        "EN 10217-7 TC1 ungeglüht HL ca. 6 m;1;0;M;85700;AAAA; ; ;"
    )
    a = datanorm.parse_artikel(zeile)
    assert a.listenpreis == Decimal("857.0000")   # nicht 85700 €


def test_artikel_preiseinheit_100():
    """Stahlhaften: 12,90 € für 100 Stück, nicht 12,90 € pro Stück."""
    zeile = (
        "A;N;STAHLHAF20;00;Stahlhaften 20cm;"
        "z.Befestigung der Matten im Erdreich;1;2;ST;1290;BLGP; ; ;"
    )
    a = datanorm.parse_artikel(zeile)
    assert a.preiseinheit == 2
    assert a.listenpreis == Decimal("0.1290")


def test_artikel_ohne_preiseinheit_faellt_auf_null():
    zeile = "A;N;X1;00;Ding;;1;;ST;100;;;;"
    a = datanorm.parse_artikel(zeile)
    assert a.preiseinheit == 0
    assert a.listenpreis == Decimal("1.0000")


def test_kein_artikelsatz():
    with pytest.raises(datanorm.DatanormFehler):
        datanorm.parse_artikel("B;N;X1;ACARO;;;")


# --- Preiseinheit -----------------------------------------------------------

@pytest.mark.parametrize(
    "cent,einheit,erwartet",
    [
        (2380, 0, "23.8000"),      # je 1
        (2380, 1, "2.3800"),       # je 10
        (1290, 2, "0.1290"),       # je 100
        (37200, 3, "0.3720"),      # je 1000: 372,00 € für 1000 Einheiten
    ],
)
def test_preis_je_einheit(cent, einheit, erwartet):
    assert datanorm.preis_je_einheit(cent, einheit) == Decimal(erwartet)


def test_unbekannte_preiseinheit():
    with pytest.raises(datanorm.DatanormFehler, match="Preiseinheit"):
        datanorm.preis_je_einheit(100, 9)


# --- Preissatz (P) ----------------------------------------------------------

def test_preissatz_mehrere_bloecke():
    zeile = (
        "P;A;RG6040640U1;1;85700;1;6800;;;;;"
        "RG6050840U1;1;107300;1;6800;;;;;"
        "RG6060950U1;1;161200;1;6800;;;;;"
    )
    preise = datanorm.parse_preise(zeile)
    assert [p.artikelnummer for p in preise] == [
        "RG6040640U1", "RG6050840U1", "RG6060950U1"
    ]
    erster = preise[0]
    assert erster.preiskennzeichen == datanorm.PREISKENNZEICHEN_LISTE
    assert erster.preis_cent == 85700
    assert erster.rabatt_kennzeichen == datanorm.RABATT_PROZENT
    assert erster.rabatt_wert == 6800


def test_preissatz_nettopreis_ohne_rabatt():
    """Preiskennzeichen 2: der Preis IST der EK, die Rabattfelder bleiben leer."""
    zeile = "P;A;LZ14301;2;229;;;;;;;LZ14301N;1;329;1;0;;;;;"
    preise = datanorm.parse_preise(zeile)
    netto, liste = preise
    assert netto.preiskennzeichen == datanorm.PREISKENNZEICHEN_NETTO
    assert netto.rabatt_kennzeichen is None
    assert liste.preiskennzeichen == datanorm.PREISKENNZEICHEN_LISTE
    assert liste.rabatt_wert == 0


# --- Einkaufspreis: die eigentliche Rechnung --------------------------------

def test_ek_edelstahlrohr_listenpreis_minus_rabatt():
    """857,00 € minus 68,00 % Rabatt = 274,24 € je Meter."""
    p = datanorm.Preis("RG6040640U1", 1, 85700, datanorm.RABATT_PROZENT, 6800)
    ek, liste = datanorm.einkaufspreis(p, preiseinheit=0)
    assert liste == Decimal("857.0000")
    assert ek == Decimal("274.2400")


def test_ek_stahlhaften_preiseinheit_und_rabatt():
    """Die teure Falle: 12,90 € je 100 Stück, minus 40 % → 0,0774 €/Stück.

    Auf zwei Nachkommastellen gerundet wären das 0,08 € — bei 1000 Stück ein
    Fehler von 2,60 € (3,4 %). Deshalb vier Nachkommastellen.
    """
    p = datanorm.Preis("STAHLHAF20", 1, 1290, datanorm.RABATT_PROZENT, 4000)
    ek, liste = datanorm.einkaufspreis(p, preiseinheit=2)
    assert liste == Decimal("0.1290")
    assert ek == Decimal("0.0774")
    assert ek != Decimal("0.08")


def test_ek_nettopreis_direkt():
    p = datanorm.Preis("LZ14301", 2, 229, None, None)
    ek, liste = datanorm.einkaufspreis(p, preiseinheit=0)
    assert ek == Decimal("2.2900")
    assert liste is None          # Nettopreis kennt keinen Listenpreis


def test_ek_rabatt_null_prozent():
    p = datanorm.Preis("LZ14301N", 1, 329, datanorm.RABATT_PROZENT, 0)
    ek, liste = datanorm.einkaufspreis(p, preiseinheit=0)
    assert ek == liste == Decimal("3.2900")


def test_ek_werkspreis_bleibt_unbekannt():
    """Ein Werkspreis ist kein Einkaufspreis. Nicht raten — None."""
    p = datanorm.Preis("X", datanorm.PREISKENNZEICHEN_WERK, 5000, None, None)
    ek, liste = datanorm.einkaufspreis(p, preiseinheit=0)
    assert ek is None
    assert liste == Decimal("50.0000")


def test_ek_rabattgruppe_ohne_tabelle_bleibt_unbekannt():
    """Rabattkennzeichen 0 verweist auf die .RAB-Datei, die uns nicht vorliegt.

    Unbekannt heißt None, nicht 0 — sonst stünde der Listenpreis als EK da und
    jede Marge wäre falsch.
    """
    p = datanorm.Preis("X", 1, 10000, datanorm.RABATT_GRUPPE, 1234)
    ek, liste = datanorm.einkaufspreis(p, preiseinheit=0)
    assert ek is None
    assert liste == Decimal("100.0000")


def test_ek_multiplikator():
    p = datanorm.Preis("X", 1, 10000, datanorm.RABATT_MULTIPLIKATOR, 850)
    ek, _ = datanorm.einkaufspreis(p, preiseinheit=0)
    assert ek == Decimal("85.0000")     # 100,00 × 0,850


def test_ek_aufschlag_wird_addiert():
    p = datanorm.Preis("X", 1, 10000, datanorm.RABATT_AUFSCHLAG, 500)
    ek, _ = datanorm.einkaufspreis(p, preiseinheit=0)
    assert ek == Decimal("105.0000")    # 100,00 + 5,00


def test_rabatt_ueber_100_prozent_wird_abgewiesen():
    with pytest.raises(datanorm.DatanormFehler, match="Rabattsatz"):
        datanorm.rabatt_anwenden(
            Decimal("100"), datanorm.RABATT_PROZENT, 12000
        )


# --- Zusatzsatz (B) ---------------------------------------------------------

def test_zusatzsatz_matchcode_ist_fabrikat():
    zeile = "B;N;PPAC12M110300;ACARO;AAASRT10298106; ;0;0;0; ; ; ;0;0; ; ;"
    b = datanorm.parse_zusatz(zeile)
    assert b.artikelnummer == "PPAC12M110300"
    assert b.matchcode == "ACARO"
    assert b.alt_artikelnummer == "AAASRT10298106"
    assert b.ean is None            # dieser Händler liefert keine EAN
    assert b.warengruppe is None


def test_zusatzsatz_ean_wird_nur_bei_echter_gtin_uebernommen():
    mit_ean = "B;N;X;MARKE;ALT; ;0;0;0;4024074403976; ; ;0;0; ; ;"
    assert datanorm.parse_zusatz(mit_ean).ean == "4024074403976"
    # "0" ist keine GTIN
    ohne = "B;N;X;MARKE;ALT; ;0;0;0;0; ; ;0;0; ; ;"
    assert datanorm.parse_zusatz(ohne).ean is None


# --- Langtext (D) -----------------------------------------------------------

def test_langtext_zeilen():
    zeile = (
        "D;N;PPAC12M110300;1;F;;Hochlast-Vollwand-Kanalrohr aus;"
        "2;F;;hochmodularem PP HM;"
    )
    artnr, zeilen = datanorm.parse_langtext(zeile)
    assert artnr == "PPAC12M110300"
    assert zeilen == [
        (1, "Hochlast-Vollwand-Kanalrohr aus"),
        (2, "hochmodularem PP HM"),
    ]


# --- Kodierung --------------------------------------------------------------

def test_cp850_ist_die_kodierung():
    """0x8E ist 'Ä' in CP850 — in ISO-8859-1 wäre es ein Steuerzeichen."""
    roh = b"B\x8eR & OLLENROTH"
    assert roh.decode(datanorm.ENCODING) == "BÄR & OLLENROTH"
    assert datanorm.ENCODING == "cp850"
