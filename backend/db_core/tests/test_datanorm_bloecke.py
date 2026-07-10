"""Der Importer streamt Artikel-Blöcke und wählt den richtigen Preissatz.

Die Stammdatei ist je Artikel gruppiert: A-Satz, dann sein B-Satz, dann seine
D-Sätze. `_artikel_bloecke` nutzt das und puffert nur einen Artikel — bei 2 Mio
Artikeln mit 14,5 Mio Langtextzeilen ist alles andere nicht speicherbar.

Beim Preis gilt: ein Artikel kann in der Preisdatei zweimal stehen, einmal als
Listenpreis mit Rabatt (Kennzeichen 1), einmal als Nettopreis (Kennzeichen 2).
Der Nettopreis ist die ausdrückliche Aussage des Händlers und gewinnt.
"""
import io
import zipfile

import pytest

from db_core.management.commands.datanorm_import import (
    _artikel_bloecke,
    _preisindex,
)
from db_core.services import datanorm

STAMM = """\
V 020726DATANORM - Datenservice - Artikelstamm  Copyright Testhaendler          04EUR
A;N;ART1;50;Erster Artikel;Zweite Zeile;1;0;ST;1000;RG01; ; ;
B;N;ART1;FABRIKAT;HERSTNR; ;0;0;0; ; ; ;0;0; ; ;
D;N;ART1;1;F;;Langtext Zeile eins;2;F;;Langtext Zeile zwei;
D;N;ART1;3;F;;Langtext Zeile drei;
A;N;ART2;00;Zweiter Artikel;;1;2;ST;1290;RG02; ; ;
B;N;ART2;MARKE2;ALT2; ;0;0;0; ; ; ;0;0; ; ;
A;N;ART3;00;Dritter ohne Zusatz;;2;0;M;500;; ; ;
"""

PREISE = """\
V 030726DATANORM - Datenservice - Preispflege    Copyright Testhaendler         04EUR
K;;201190; ;
P;A;ART1;1;1000;1;3300;;;;;ART2;1;1290;1;4000;;;;;
P;A;ART3;1;500;1;1000;;;;;ART3;2;450;;;;;;;
"""


def _zip(tmp_path, name, inhalt):
    pfad = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(pfad, "w") as z:
        z.writestr(f"{name}.001", inhalt.encode(datanorm.ENCODING))
    return str(pfad)


@pytest.fixture
def stamm_zip(tmp_path):
    return _zip(tmp_path, "datanorm", STAMM)


@pytest.fixture
def preis_zip(tmp_path):
    return _zip(tmp_path, "datpreis", PREISE)


def test_bloecke_gruppieren_artikel_zusatz_und_langtext(stamm_zip):
    bloecke = list(_artikel_bloecke(stamm_zip))
    assert [a.artikelnummer for a, _, _ in bloecke] == ["ART1", "ART2", "ART3"]

    a1, b1, text1 = bloecke[0]
    assert a1.bezeichnung == "Erster Artikel Zweite Zeile"
    assert b1.matchcode == "FABRIKAT"
    assert b1.alt_artikelnummer == "HERSTNR"
    # Zeilen aus zwei D-Saetzen, nach Zeilennummer sortiert zusammengefuegt
    assert text1 == "Langtext Zeile eins\nLangtext Zeile zwei\nLangtext Zeile drei"


def test_block_ohne_zusatz_und_ohne_langtext(stamm_zip):
    _, b3, text3 = list(_artikel_bloecke(stamm_zip))[2]
    assert b3 is None
    assert text3 is None


def test_langtext_wandert_nicht_zum_naechsten_artikel(stamm_zip):
    """ART2 hat keine D-Saetze — sein Langtext muss None sein, nicht der von ART1."""
    _, _, text2 = list(_artikel_bloecke(stamm_zip))[1]
    assert text2 is None


def test_preisindex_netto_schlaegt_liste(preis_zip):
    liste, netto = _preisindex(preis_zip)
    assert set(liste) == {"ART1", "ART2", "ART3"}
    assert set(netto) == {"ART3"}
    # ART3 steht doppelt: Liste 5,00 - 10 % und Netto 4,50. Netto gewinnt.
    assert netto["ART3"].preis_cent == 450
    assert liste["ART3"].preis_cent == 500


def test_ek_je_artikel_aus_stamm_und_preisdatei(stamm_zip, preis_zip):
    """Der EK entsteht aus dem Preissatz UND der Preiseinheit des Stammsatzes."""
    from decimal import Decimal

    liste, netto = _preisindex(preis_zip)
    bloecke = {a.artikelnummer: a for a, _, _ in _artikel_bloecke(stamm_zip)}

    # ART1: je 1, 10,00 EUR - 33 %
    ek, lp = datanorm.einkaufspreis(liste["ART1"], bloecke["ART1"].preiseinheit)
    assert (lp, ek) == (Decimal("10.0000"), Decimal("6.7000"))

    # ART2: je 100 -> 0,1290 EUR/Stueck, - 40 %
    ek, lp = datanorm.einkaufspreis(liste["ART2"], bloecke["ART2"].preiseinheit)
    assert (lp, ek) == (Decimal("0.1290"), Decimal("0.0774"))

    # ART3: Nettopreis gewinnt, kein Listenpreis
    ek, lp = datanorm.einkaufspreis(netto["ART3"], bloecke["ART3"].preiseinheit)
    assert (lp, ek) == (None, Decimal("4.5000"))
