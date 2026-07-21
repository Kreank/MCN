"""Standort-Zuordnung: Die Einheit bestimmt ihr Gebäude (Befund I11).

Vorher war das eine Bringschuld jedes Aufrufers: Wer eine Einheit angab, musste
ihr Gebäude mitschicken, sonst wies `ensure_standort` mit „Eine Einheit setzt
ein Gebäude voraus" ab — **obwohl die Funktion den Wert an derselben Stelle
bereits las**, um ihn zu vergleichen.

Die Folge war eine dreifach kopierte Auswahlkaskade im Frontend (Raum-Editor,
Anlagen-Dialog, Plantafel), die jeder weitere Client ein weiteres Mal
nachbauen müsste — inklusive KI-Assistent und Import.

Die Konsistenzprüfung bleibt unverändert scharf: Ein **falsches** Gebäude wird
weiterhin abgewiesen, nur ein **fehlendes** wird jetzt ergänzt.
"""
from decimal import Decimal

import pytest

from db_core.models import Room, TechnicalAsset
from db_core.services import anlage as anlage_service
from db_core.services import property as property_service
from db_core.services import raum as raum_service
from db_core.services._validation import ensure_standort


@pytest.fixture
def objekt(app_user):
    prop = property_service.create_property(
        app_user.id, name="Ableitungs-Objekt", property_type="WEG",
        street="Feldweg", house_number="3", postal_code="10115", city="Berlin",
    )
    haus = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    wohnung = property_service.add_unit(
        app_user.id, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="WE 1", storey="3. OG",
    )
    return {"actor": app_user, "prop": prop, "haus": haus, "wohnung": wohnung}


# --- Die Regel selbst ------------------------------------------------------


@pytest.mark.django_db
def test_gebaeude_wird_aus_der_einheit_abgeleitet(objekt):
    b, u = ensure_standort(objekt["prop"].id, None, objekt["wohnung"].id)
    assert b == objekt["haus"].id
    assert u == objekt["wohnung"].id


@pytest.mark.django_db
def test_falsches_gebaeude_wird_weiterhin_abgewiesen(objekt):
    """Ableiten heißt nicht nachgeben: Die Konsistenzprüfung bleibt scharf."""
    zweites = property_service.add_building(
        objekt["actor"].id, property_id=objekt["prop"].id, building_number="2"
    )
    with pytest.raises(ValueError, match="gehört nicht zum angegebenen Gebäude"):
        ensure_standort(objekt["prop"].id, zweites.id, objekt["wohnung"].id)


@pytest.mark.django_db
def test_unbekannte_einheit_bleibt_ein_fehler(objekt):
    import uuid as _uuid

    with pytest.raises(ValueError, match="existiert nicht"):
        ensure_standort(objekt["prop"].id, None, _uuid.uuid4())


@pytest.mark.django_db
def test_gebaeude_ohne_einheit_bleibt_erlaubt(objekt):
    """„Gebäude allgemein" (Keller, Technik) ist ein gültiger Zustand."""
    b, u = ensure_standort(objekt["prop"].id, objekt["haus"].id, None)
    assert b == objekt["haus"].id
    assert u is None


@pytest.mark.django_db
def test_ganz_ohne_zuordnung_bleibt_erlaubt(objekt):
    """Altbestand ohne erfasste Struktur."""
    assert ensure_standort(objekt["prop"].id, None, None) == (None, None)


# --- Und die Aufrufer schreiben es auch ------------------------------------


@pytest.mark.django_db
def test_raum_bekommt_das_gebaeude_geschrieben(objekt):
    """Nicht nur prüfen — der abgeleitete Wert muss in der Zeile landen.

    Sonst schlüge der zusammengesetzte FK `(unit_id, building_id)` aus 0086 zu.
    """
    raum = raum_service.create_room(
        objekt["actor"].id,
        objekt["prop"].id,
        {
            "name": "Wohnzimmer",
            "floor_area_m2": Decimal("20.000"),
            "room_height_m": Decimal("2.500"),
            "unit_id": objekt["wohnung"].id,
        },
    )
    zeile = Room.objects.get(id=raum.id)
    assert zeile.unit_id == objekt["wohnung"].id
    assert zeile.building_id == objekt["haus"].id


@pytest.mark.django_db
def test_anlage_bekommt_das_gebaeude_geschrieben(objekt):
    anlage = anlage_service.create_asset(
        objekt["actor"].id,
        objekt["prop"].id,
        {
            "name": "Therme",
            "asset_type": "THERME_HEIZUNG",
            "unit_id": objekt["wohnung"].id,
        },
    )
    zeile = TechnicalAsset.objects.get(id=anlage.id)
    assert zeile.unit_id == objekt["wohnung"].id
    assert zeile.building_id == objekt["haus"].id


@pytest.mark.django_db
def test_raum_umhaengen_ohne_gebaeude_geht(objekt):
    """Auch beim PATCH: Wer nur die Einheit umsetzt, muss nicht wissen, wohin."""
    raum = raum_service.create_room(
        objekt["actor"].id,
        objekt["prop"].id,
        {
            "name": "Bad",
            "floor_area_m2": Decimal("8.000"),
            "room_height_m": Decimal("2.500"),
        },
    )
    assert Room.objects.get(id=raum.id).building_id is None

    raum_service.update_room(
        objekt["actor"].id, raum.id, {"unit_id": objekt["wohnung"].id}
    )
    zeile = Room.objects.get(id=raum.id)
    assert zeile.unit_id == objekt["wohnung"].id
    assert zeile.building_id == objekt["haus"].id
