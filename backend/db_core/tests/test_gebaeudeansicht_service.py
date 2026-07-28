"""Gebäudeansicht — das Objekt als Haus (`services/gebaeudeansicht.py`).

Geprüft wird, was das Bild trägt: die Reihenfolge der Etagen (oben nach unten),
die Zuordnung der Technik (Einheit / Gebäude / gar nicht) und die Belegung.

Der teuerste Fehler wäre eine **geratene** Etage: Wer wegen „2" statt „2. OG" im
falschen Stock klingelt, hat die Fahrt umsonst gemacht. Deshalb liegt das
Ungedeutete sichtbar unten und wird nicht einsortiert.
"""
from datetime import date

import pytest

from db_core.services import anlage as anlage_service
from db_core.services import belegung as belegung_service
from db_core.services import gebaeudeansicht as ansicht_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


@pytest.fixture
def muensterstrasse(app_user):
    """Münsterstraße 24: Vorderhaus (3 Etagen) und Hinterhaus."""
    prop = property_service.create_property(
        app_user.id, name="Münsterstraße 24", property_type="RENTAL_PROPERTY",
        street="Münsterstraße", house_number="24", postal_code="44145", city="Dortmund",
    )
    vorderhaus = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    hinterhaus = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="2", name="Hinterhaus"
    )

    einheiten = {}
    for nr, etage in (("1", "EG"), ("2", "1. OG"), ("3", "1. OG"), ("4", "DG")):
        einheiten[nr] = property_service.add_unit(
            app_user.id, building_id=vorderhaus.id, property_id=prop.id,
            unit_type="APARTMENT", unit_number=nr, storey=etage,
        )
    einheiten["H1"] = property_service.add_unit(
        app_user.id, building_id=hinterhaus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="H1", storey="EG",
    )
    # Technikraum: keine Belegung möglich — er darf nicht wie Leerstand aussehen.
    einheiten["T"] = property_service.add_unit(
        app_user.id, building_id=vorderhaus.id, property_id=prop.id,
        unit_type="TECHNICAL_ROOM", unit_number="T1", storey="KG",
    )
    return {
        "actor": app_user, "prop": prop,
        "vorderhaus": vorderhaus, "hinterhaus": hinterhaus, "einheiten": einheiten,
    }


def _haus(daten, name):
    return next(h for h in daten["haeuser"] if h["gebaeude"].name == name)


@pytest.mark.django_db
def test_haeuser_und_etagen_stehen_von_oben_nach_unten(muensterstrasse):
    daten = ansicht_service.ansicht(muensterstrasse["prop"].id)
    assert [h["gebaeude"].name for h in daten["haeuser"]] == ["Vorderhaus", "Hinterhaus"]

    etagen = [e["label"] for e in _haus(daten, "Vorderhaus")["etagen"]]
    assert etagen == ["DG", "1. OG", "EG", "KG"]


@pytest.mark.django_db
def test_mehrere_wohnungen_auf_einer_etage_liegen_im_selben_band(muensterstrasse):
    daten = ansicht_service.ansicht(muensterstrasse["prop"].id)
    og = next(e for e in _haus(daten, "Vorderhaus")["etagen"] if e["label"] == "1. OG")
    assert sorted(x["einheit"].unit_number for x in og["einheiten"]) == ["2", "3"]


@pytest.mark.django_db
def test_undeutbare_etage_landet_unten_und_behaelt_ihren_text(muensterstrasse):
    property_service.add_unit(
        muensterstrasse["actor"].id,
        building_id=muensterstrasse["vorderhaus"].id,
        property_id=muensterstrasse["prop"].id,
        unit_type="APARTMENT", unit_number="9", storey="Gartenhaus links",
    )
    daten = ansicht_service.ansicht(muensterstrasse["prop"].id)
    etagen = _haus(daten, "Vorderhaus")["etagen"]
    assert etagen[-1]["label"] == "Gartenhaus links"
    assert etagen[-1]["gedeutet"] is False


@pytest.mark.django_db
def test_einheit_ohne_etage_bekommt_ein_eigenes_band(muensterstrasse):
    property_service.add_unit(
        muensterstrasse["actor"].id,
        building_id=muensterstrasse["hinterhaus"].id,
        property_id=muensterstrasse["prop"].id,
        unit_type="APARTMENT", unit_number="H2",
    )
    daten = ansicht_service.ansicht(muensterstrasse["prop"].id)
    etagen = _haus(daten, "Hinterhaus")["etagen"]
    assert etagen[-1]["label"] == "Ohne Etagenangabe"
    assert etagen[-1]["gedeutet"] is False


@pytest.mark.django_db
def test_technikraum_ist_nicht_belegbar(muensterstrasse):
    daten = ansicht_service.ansicht(muensterstrasse["prop"].id)
    kg = next(e for e in _haus(daten, "Vorderhaus")["etagen"] if e["label"] == "KG")
    assert kg["einheiten"][0]["belegbar"] is False


# --- Technik ----------------------------------------------------------------

@pytest.mark.django_db
def test_anlagen_liegen_an_einheit_gebaeude_oder_nirgends(muensterstrasse):
    actor, prop = muensterstrasse["actor"], muensterstrasse["prop"]
    anlage_service.create_asset(
        actor.id, prop.id,
        {
            "name": "Etagentherme WE 2",
            "asset_type": "THERME_COMBI",
            "supply_type": "DEZENTRAL",
            "unit_id": muensterstrasse["einheiten"]["2"].id,
        },
    )
    anlage_service.create_asset(
        actor.id, prop.id,
        {
            "name": "Zentralheizung",
            "asset_type": "KESSEL_HEIZUNG",
            "supply_type": "ZENTRAL",
            "building_id": muensterstrasse["vorderhaus"].id,
        },
    )
    anlage_service.create_asset(
        actor.id, prop.id, {"name": "Noch nicht verortet", "asset_type": "SONSTIGE"}
    )

    daten = ansicht_service.ansicht(prop.id)
    vorderhaus = _haus(daten, "Vorderhaus")

    assert [a.name for a in vorderhaus["technik"]] == ["Zentralheizung"]
    we2 = next(
        x
        for e in vorderhaus["etagen"]
        for x in e["einheiten"]
        if x["einheit"].unit_number == "2"
    )
    assert [a.name for a in we2["anlagen"]] == ["Etagentherme WE 2"]
    # Nicht verortet heißt sichtbar daneben, nicht verschwunden.
    assert [a.name for a in daten["anlagen_ohne_gebaeude"]] == ["Noch nicht verortet"]


@pytest.mark.django_db
def test_stillgelegte_anlage_taucht_im_haus_nicht_auf(muensterstrasse):
    actor, prop = muensterstrasse["actor"], muensterstrasse["prop"]
    alt = anlage_service.create_asset(
        actor.id, prop.id,
        {
            "name": "Alter Kessel",
            "asset_type": "KESSEL_HEIZUNG",
            "building_id": muensterstrasse["vorderhaus"].id,
        },
    )
    anlage_service.update_asset(actor.id, alt.id, {"status": "INAKTIV"})
    daten = ansicht_service.ansicht(prop.id)
    assert _haus(daten, "Vorderhaus")["technik"] == []


# --- Belegung ---------------------------------------------------------------

@pytest.mark.django_db
def test_bewohner_kommen_aus_der_belegung(muensterstrasse):
    actor, prop = muensterstrasse["actor"], muensterstrasse["prop"]
    mieter = identity_service.create_person(
        actor.id, first_name="Maria", last_name="Mieterin"
    )
    belegung_service.create_belegung(
        actor.id,
        unit_id=muensterstrasse["einheiten"]["1"].id,
        occupancy_type="RENTED",
        valid_from=date(2026, 1, 1),
        mieter=[{"party_id": mieter.id, "role": "CONTRACTUAL_TENANT"}],
    )

    daten = ansicht_service.ansicht(prop.id)
    we1 = next(
        x
        for e in _haus(daten, "Vorderhaus")["etagen"]
        for x in e["einheiten"]
        if x["einheit"].unit_number == "1"
    )
    assert [p.party.display_name for p in we1["bewohner"]] == ["Maria Mieterin"]

    # Ohne Recht auf `tenure` bleibt dieselbe Struktur — nur ohne Bewohner.
    ohne = ansicht_service.ansicht(prop.id, mit_belegung=False)
    we1_ohne = next(
        x
        for e in _haus(ohne, "Vorderhaus")["etagen"]
        for x in e["einheiten"]
        if x["einheit"].unit_number == "1"
    )
    assert we1_ohne["bewohner"] == []
