"""Katalogprofile: welches DATANORM-Feld was bedeutet, und wer die nackte
Artikelnummer bekommt.

Diese Regeln haben einen Schaden hinter sich: Ein einheitliches Mapping für alle
Kataloge hat 2.043.336 B&O-Artikeln eine „Hersteller-Nr." gegeben, die es
nirgends gibt (B&Os hauseigene Katalognummer), und als „Hersteller" den
Matchcode eingetragen. Die Tests halten fest, dass beides nicht zurückkommt.
"""
from dataclasses import dataclass

from db_core.services import datanorm_katalog as katalog


@dataclass(frozen=True)
class _A:
    artikelnummer: str


@dataclass(frozen=True)
class _B:
    matchcode: str | None
    alt_artikelnummer: str | None


def test_grosshandel_leitet_keine_herstellernummer_ab():
    p = katalog.profil("GROSSHAENDLER")
    felder = katalog.identitaetsfelder(p, _A("CUS15H"), _B("CUSSH01510", "ZRB2071510"))
    assert felder["manufacturer_number"] is None
    # Der Matchcode ist ein Suchcode, kein Hersteller — er darf nur ins eigene Feld.
    assert felder["manufacturer_name"] is None
    assert felder["matchcode"] == "CUSSH01510"


def test_grosshandel_sichert_die_katalognummer_an_der_referenz():
    p = katalog.profil("GROSSHAENDLER")
    assert katalog.katalog_id(p, _B("GEBERIT", "ARESRT10018217")) == "ARESRT10018217"


def test_hersteller_nimmt_die_artikelnummer_als_herstellernummer():
    p = katalog.profil("HERSTELLER", hersteller_name="BOSCH")
    felder = katalog.identitaetsfelder(p, _A("10000946"), _B("EINLEGEBLENDE", "1-000-094-6"))
    assert felder["manufacturer_number"] == "10000946"
    assert felder["manufacturer_name"] == "BOSCH"
    # Auch hier gilt: die Kurzbezeichnung ist kein Hersteller.
    assert felder["matchcode"] == "EINLEGEBLENDE"
    # Herstellerkataloge führen keine fremde Katalognummer.
    assert katalog.katalog_id(p, _B("EINLEGEBLENDE", "1-000-094-6")) is None


def test_unbekannte_katalogart_behauptet_nichts():
    """Im Zweifel leer statt falsch — ein leeres Feld ist ehrlich."""
    for wert in (None, "", "IRGENDWAS"):
        p = katalog.profil(wert)
        felder = katalog.identitaetsfelder(p, _A("X1"), _B("MC", "ALT1"))
        assert felder["manufacturer_number"] is None
        assert felder["manufacturer_name"] is None


def test_leitkatalog_behaelt_die_nackte_nummer():
    """B&O ist der Bestellkatalog: dessen Nummer darf nie verfremdet werden."""
    belegt = {"509010"}.__contains__
    assert katalog.artikelnummer("509010", "bo", belegt=belegt) == "509010"


def test_fremdkatalog_weicht_bei_kollision_aus():
    belegt = {"509010"}.__contains__
    assert katalog.artikelnummer("509010", "vaillant", belegt=belegt) == "509010-vaillant"


def test_freie_nummer_bleibt_unveraendert():
    assert katalog.artikelnummer("CUS15H", "vaillant", belegt=lambda _: False) == "CUS15H"


def test_ausweichnummer_zaehlt_hoch_und_weicht_immer_aus():
    vergeben = {"509010", "509010-bo", "509010-bo-2"}
    assert katalog.ausweichnummer("509010", "bo", belegt=vergeben.__contains__) == "509010-bo-3"
