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


# --- Lage in der Etage ------------------------------------------------------
#
# Der Praxisbefund (Sascha, 2026-07-29): Erfasst wird nicht „EG", sondern
# „EG links" — das Etagenfeld ist das einzige, in das die Lage passt. Vorher
# ergab das acht Bänder mit je einer Wohnung, alphabetisch sortiert: das EG
# stand über dem 3. OG.


@pytest.fixture
def haus_mit_lagen(app_user):
    """Ein Haus, dessen Etagenfeld die Lage mitträgt („EG links")."""
    prop = property_service.create_property(
        app_user.id, name="Lagehaus", property_type="RENTAL_PROPERTY",
        street="Beispielweg", house_number="3", postal_code="44145", city="Dortmund",
    )
    haus = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    for nr, etage in (
        ("WE 1", "EG rechts"),
        ("WE 2", "EG links"),
        ("WE 3", "1. OG links"),
        ("WE 4", "1. OG rechts"),
        ("WE 5", "2. OG Mitte"),
        ("WE 6", "2. OG li"),
        ("WE 7", "2. OG re"),
        ("WE 8", "3. OG links"),
    ):
        property_service.add_unit(
            app_user.id, building_id=haus.id, property_id=prop.id,
            unit_type="APARTMENT", unit_number=nr, storey=etage,
        )
    return {"actor": app_user, "prop": prop, "haus": haus}


@pytest.mark.django_db
def test_lage_im_etagenfeld_zerreisst_das_haus_nicht(haus_mit_lagen):
    """„EG links" + „EG rechts" = **eine** Etage mit zwei Wohnungen."""
    daten = ansicht_service.ansicht(haus_mit_lagen["prop"].id)
    etagen = _haus(daten, "Vorderhaus")["etagen"]

    assert [e["label"] for e in etagen] == ["3. OG", "2. OG", "1. OG", "EG"]
    assert all(e["gedeutet"] for e in etagen)


@pytest.mark.django_db
def test_einheiten_stehen_von_links_nach_rechts(haus_mit_lagen):
    """Links, Mitte, rechts — in der Reihenfolge, in der man davorsteht."""
    daten = ansicht_service.ansicht(haus_mit_lagen["prop"].id)
    etagen = {e["label"]: e for e in _haus(daten, "Vorderhaus")["etagen"]}

    eg = etagen["EG"]
    assert [x["einheit"].unit_number for x in eg["einheiten"]] == ["WE 2", "WE 1"]
    assert [x["lage"] for x in eg["einheiten"]] == ["links", "rechts"]

    # Drei Wohnungen auf der Etage, auch über die Kürzel „li"/„re".
    og2 = etagen["2. OG"]
    assert [x["lage"] for x in og2["einheiten"]] == ["links", "Mitte", "rechts"]
    assert [x["einheit"].unit_number for x in og2["einheiten"]] == ["WE 6", "WE 5", "WE 7"]

    # Der erfasste Text geht nicht verloren — er hängt an der Einheit.
    assert eg["einheiten"][0]["etage_text"] == "EG links"


@pytest.mark.django_db
def test_ohne_lage_zaehlt_die_nummer_natuerlich(muensterstrasse):
    """„WE 10" steht hinter „WE 2" — Textsortierung stellt es davor."""
    actor, prop = muensterstrasse["actor"], muensterstrasse["prop"]
    for nr in ("WE 10", "WE 2", "WE 1"):
        property_service.add_unit(
            actor.id, building_id=muensterstrasse["hinterhaus"].id, property_id=prop.id,
            unit_type="APARTMENT", unit_number=nr, storey="2. OG",
        )
    daten = ansicht_service.ansicht(prop.id)
    og = next(e for e in _haus(daten, "Hinterhaus")["etagen"] if e["label"] == "2. OG")
    assert [x["einheit"].unit_number for x in og["einheiten"]] == ["WE 1", "WE 2", "WE 10"]


@pytest.mark.django_db
def test_zwei_schreibweisen_ergeben_ein_band_und_werden_benannt(muensterstrasse):
    """„2. OG" und „2.OG" gehören in dieselbe Etage — der Unterschied bleibt sichtbar."""
    actor, prop = muensterstrasse["actor"], muensterstrasse["prop"]
    for nr, etage in (("X1", "2. OG"), ("X2", "2. OG"), ("X3", "2.OG")):
        property_service.add_unit(
            actor.id, building_id=muensterstrasse["hinterhaus"].id, property_id=prop.id,
            unit_type="APARTMENT", unit_number=nr, storey=etage,
        )
    daten = ansicht_service.ansicht(prop.id)
    og = [e for e in _haus(daten, "Hinterhaus")["etagen"] if e["ordnung"] == 2.0]
    assert len(og) == 1
    assert og[0]["label"] == "2. OG"  # die häufigere Schreibweise beschriftet
    assert sorted(og[0]["schreibweisen"]) == ["2. OG", "2.OG"]


@pytest.mark.django_db
def test_etage_aus_der_nummer_nur_mit_echtem_etagenwort(muensterstrasse):
    """Leeres Etagenfeld: „EG links" in der Nummer hilft — „3" niemals.

    „9" heißt in der Nummer *Wohnung 9*. Wer daraus das 9. OG macht, schickt den
    Monteur in den falschen Stock — deshalb bleibt sie im Band ohne Etage.
    """
    actor, prop = muensterstrasse["actor"], muensterstrasse["prop"]
    for nr in ("EG rechts", "EG links", "9"):
        property_service.add_unit(
            actor.id, building_id=muensterstrasse["hinterhaus"].id, property_id=prop.id,
            unit_type="COMMERCIAL", unit_number=nr,
        )
    daten = ansicht_service.ansicht(prop.id)
    etagen = _haus(daten, "Hinterhaus")["etagen"]

    eg = next(e for e in etagen if e["ordnung"] == 0.0)
    assert [x["einheit"].unit_number for x in eg["einheiten"]] == [
        "EG links", "EG rechts", "H1",
    ]
    # Die Ableitung wird ausgewiesen, nicht verschwiegen.
    assert eg["abgeleitet"] is True

    ohne = next(e for e in etagen if e["label"] == "Ohne Etagenangabe")
    assert [x["einheit"].unit_number for x in ohne["einheiten"]] == ["9"]


@pytest.mark.django_db
def test_lage_darf_auch_in_der_nummer_stehen(muensterstrasse):
    """Etage im Etagenfeld, Lage in der Nummer („Laden links") — auch das ordnet."""
    actor, prop = muensterstrasse["actor"], muensterstrasse["prop"]
    for nr in ("Laden rechts", "Laden links"):
        property_service.add_unit(
            actor.id, building_id=muensterstrasse["hinterhaus"].id, property_id=prop.id,
            unit_type="COMMERCIAL", unit_number=nr, storey="EG",
        )
    daten = ansicht_service.ansicht(prop.id)
    eg = next(e for e in _haus(daten, "Hinterhaus")["etagen"] if e["label"] == "EG")
    assert [x["einheit"].unit_number for x in eg["einheiten"]] == [
        "Laden links", "Laden rechts", "H1",
    ]
    assert eg["abgeleitet"] is False  # die Etage stand im Feld, nur die Lage nicht


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
