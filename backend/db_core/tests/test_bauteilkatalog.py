"""Bauteilkatalog: die Vorlage ist eine KOPIERQUELLE, kein Verweis.

Der Kern dieses Slices steht in `test_katalogkorrektur_aendert_die_heizlast_nicht`
und in nichts anderem:

    Ein Betrieb korrigiert den U-Wert der Vorlage „Fenster, 2-fach" von 2,8 auf
    1,4. Die Heizlast eines Objekts, das er dem Kunden vor drei Monaten
    vorgerechnet und angeboten hat, **darf sich dadurch nicht ändern**.

Wäre `template_id` ein Verweis und läse der Rechner den Katalog, verschöbe sich
diese Zahl rückwirkend und lautlos — dasselbe Unglück, gegen das die Belegposition
als Kopie gebaut ist. Deshalb wird der U-Wert beim Erfassen **in die Zeile
kopiert**; `template_id` bleibt ein Herkunftsvermerk.

Die zweite Haltung (Modulkopf 0090): Der Katalog wird **ohne U-Werte**
ausgeliefert (Normrecht). Eine Vorlage ohne Wert ist damit der Normalzustand — sie
verhält sich wie ein fehlender U-Wert an der Wand: die Heizlast ist **unbekannt,
nicht 0**.
"""
from decimal import Decimal

import pytest

from db_core.models import ComponentTemplate, RoomSurface
from db_core.services import bauteilkatalog as katalog
from db_core.services import property as property_service
from db_core.services import raum as raum_service


# --- Hilfen ----------------------------------------------------------------

def _property(actor):
    prop = property_service.create_property(
        actor.id, name="Katalog-Objekt", property_type="EINFAMILIENHAUS",
        street="Feldweg", house_number="3", postal_code="10115", city="Berlin",
    )
    # Die Auslegungs-Außentemperatur hängt am OBJEKT (0089) — ohne sie bliebe die
    # Heizlast unbekannt, und dieser Test prüfte nichts.
    return raum_service.set_auslegung(
        actor.id, prop.id, {"design_outdoor_temp_c": Decimal("-12.0")}
    )


def _raum(actor, prop):
    return raum_service.create_room(actor.id, prop.id, {
        "name": "Wohnzimmer",
        "floor_area_m2": Decimal("20.000"),
        "room_height_m": Decimal("2.500"),
        "indoor_temp_c": Decimal("20.0"),
        "air_change_rate": Decimal("0.50"),
    })


def _wand_vorlage(actor, u_value=None, name="Außenwand, Prüfstand"):
    return katalog.create_template(actor.id, {
        "kind": "FLAECHE", "name": name,
        "default_surface_type": "AUSSENWAND", "u_value": u_value,
    })


# --- Auslieferungszustand ---------------------------------------------------

@pytest.mark.django_db
def test_seed_traegt_namen_aber_keine_u_werte(app_user):
    """Normrecht: MCN liefert Namen, keine DIN-Tabellenwerte."""
    vorlagen = katalog.list_templates()
    assert vorlagen, "Der Seed-Katalog aus 0090 ist leer."
    assert any(t.name == "Fenster, Doppelkastenfenster" for t in vorlagen)
    assert all(t.u_value is None for t in vorlagen), (
        "Der Katalog darf OHNE U-Werte ausgeliefert werden — sonst stehen "
        "DIN-Tabellenwerte im Produkt."
    )


@pytest.mark.django_db
def test_filter_nach_gattung_und_status(app_user):
    flaechen = katalog.list_templates(kind="FLAECHE")
    oeffnungen = katalog.list_templates(kind="OEFFNUNG")
    assert flaechen and oeffnungen
    assert all(t.kind == "FLAECHE" for t in flaechen)
    assert all(t.default_opening_type is None for t in flaechen)

    stillgelegt = katalog.update_template(
        app_user.id, flaechen[0].id, {"status": "INAKTIV"}
    )
    assert stillgelegt.status == "INAKTIV"
    # Nicht mehr wählbar …
    assert stillgelegt.id not in {t.id for t in katalog.list_templates(kind="FLAECHE")}
    # … aber weiter lesbar: bestehende Aufmaße zeigen darauf ("aus: …").
    alle = katalog.list_templates(kind="FLAECHE", nur_aktive=False)
    assert stillgelegt.id in {t.id for t in alle}


# --- DIE Invariante: Kopie, kein Verweis -----------------------------------

@pytest.mark.django_db
def test_u_wert_wird_kopiert_nicht_verlinkt(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    vorlage = _wand_vorlage(app_user, u_value=Decimal("2.800"))

    raum_service.set_aufbau(app_user.id, room.id, [{
        "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
        "gross_area_m2": Decimal("10.000"), "template_id": vorlage.id,
    }], [])

    wand = RoomSurface.objects.get(room_id=room.id)
    # Der Wert steht in der ZEILE, nicht nur im Katalog.
    assert wand.u_value == Decimal("2.800")
    # …und die Herkunft ist vermerkt.
    assert wand.template_id == vorlage.id


@pytest.mark.django_db
def test_katalogkorrektur_aendert_die_heizlast_nicht(app_user):
    """DER Test dieses Slices.

    Wer den Katalog korrigiert, korrigiert **nicht** rückwirkend das, was der
    Kunde schon auf dem Tisch hatte.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop)
    vorlage = _wand_vorlage(app_user, u_value=Decimal("2.800"))

    raum_service.set_aufbau(app_user.id, room.id, [{
        "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
        "gross_area_m2": Decimal("10.000"), "template_id": vorlage.id,
    }], [])
    vorher = raum_service.kennzahlen(raum_service.get_room(room.id))
    # 2,8 × 10 m² × 1,0 × (20 − (−12)) = 896,0 W
    assert vorher["transmission_w"] == Decimal("896.0")

    # Der Betrieb korrigiert den Katalog — drei Monate später.
    katalog.update_template(app_user.id, vorlage.id, {"u_value": Decimal("1.400")})
    assert ComponentTemplate.objects.get(id=vorlage.id).u_value == Decimal("1.400")

    nachher = raum_service.kennzahlen(raum_service.get_room(room.id))
    assert nachher["transmission_w"] == vorher["transmission_w"], (
        "Eine Katalogkorrektur hat die Heizlast eines bestehenden Aufmaßes "
        "verschoben — die Vorlage wurde als VERWEIS gelesen, nicht als Kopie."
    )
    assert RoomSurface.objects.get(room_id=room.id).u_value == Decimal("2.800")


@pytest.mark.django_db
def test_eigener_u_wert_schlaegt_die_vorlage(app_user):
    """Ein abweichender Messwert gewinnt gegen den Katalog."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    vorlage = _wand_vorlage(app_user, u_value=Decimal("2.800"))

    raum_service.set_aufbau(app_user.id, room.id, [{
        "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
        "gross_area_m2": Decimal("10.000"), "template_id": vorlage.id,
        "u_value": Decimal("0.900"),
    }], [])

    wand = RoomSurface.objects.get(room_id=room.id)
    assert wand.u_value == Decimal("0.900")
    assert wand.template_id == vorlage.id  # Herkunft bleibt vermerkt


@pytest.mark.django_db
def test_vorlage_ohne_u_wert_heizlast_unbekannt_nicht_null(app_user):
    """Der Auslieferungszustand ist kein Fehler — aber auch keine 0 W."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    vorlage = _wand_vorlage(app_user, u_value=None)

    raum_service.set_aufbau(app_user.id, room.id, [{
        "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
        "gross_area_m2": Decimal("10.000"), "template_id": vorlage.id,
    }], [])

    wand = RoomSurface.objects.get(room_id=room.id)
    assert wand.u_value is None
    assert wand.template_id == vorlage.id

    k = raum_service.kennzahlen(raum_service.get_room(room.id))
    assert k["transmission_w"] is None, "Ein fehlender U-Wert ist NICHT 0 W."
    assert k["heizlast_huellflaeche_w"] is None
    assert "U-Wert" in k["unbekannt_grund"]


@pytest.mark.django_db
def test_oeffnung_uebernimmt_den_u_wert_der_vorlage(app_user):
    prop = _property(app_user)
    room = _raum(app_user, prop)
    fenster = katalog.create_template(app_user.id, {
        "kind": "OEFFNUNG", "name": "Fenster, Prüfstand",
        "default_opening_type": "FENSTER", "u_value": Decimal("1.300"),
    })

    room = raum_service.set_aufbau(app_user.id, room.id, [{
        "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
        "gross_area_m2": Decimal("10.000"), "u_value": Decimal("0.800"),
    }], [{
        "surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
        "width_m": Decimal("2.000"), "height_m": Decimal("1.000"),
        "template_id": fenster.id,
    }])

    o = list(room.openings.all())[0]
    assert o.u_value == Decimal("1.300")
    assert o.template_id == fenster.id


# --- Fehlbedienung → 422, nicht 500 ----------------------------------------

@pytest.mark.django_db
def test_unbekannte_vorlage_wird_abgewiesen(app_user):
    import uuid as _uuid
    prop = _property(app_user)
    room = _raum(app_user, prop)
    with pytest.raises(ValueError, match="unbekannte Bauteilvorlage"):
        raum_service.set_aufbau(app_user.id, room.id, [{
            "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
            "gross_area_m2": Decimal("10.000"), "template_id": _uuid.uuid4(),
        }], [])


@pytest.mark.django_db
def test_inaktive_vorlage_bleibt_im_bestand_bearbeitbar(app_user):
    """Stilllegen darf fremde Aufmaße nicht lahmlegen.

    `set_aufbau` ersetzt den GANZEN Satz — der Editor schickt bei jeder Änderung
    auch die Zeilen mit, die schon standen. Würde eine stillgelegte Vorlage hier
    abgewiesen, ließe sich ein Raum, dessen Wand auf sie zeigt, nie wieder
    speichern. `status` steuert die **Auswahl**, nicht den Bestand.
    """
    prop = _property(app_user)
    room = _raum(app_user, prop)
    vorlage = _wand_vorlage(app_user, u_value=Decimal("2.800"))
    wand = {
        "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
        "gross_area_m2": Decimal("10.000"), "template_id": vorlage.id,
    }
    raum_service.set_aufbau(app_user.id, room.id, [wand], [])

    katalog.update_template(app_user.id, vorlage.id, {"status": "INAKTIV"})

    # Erneut speichern (z. B. weil ein Fenster dazukommt) — muss durchgehen.
    room = raum_service.set_aufbau(app_user.id, room.id, [wand], [{
        "surface_ref": "s1", "opening_type": "FENSTER", "quantity": 1,
        "width_m": Decimal("1.000"), "height_m": Decimal("1.000"),
        "u_value": Decimal("1.300"),
    }])
    assert list(room.surfaces.all())[0].template_id == vorlage.id


@pytest.mark.django_db
def test_falsche_gattung_wird_abgewiesen(app_user):
    """Eine Fenstervorlage an einer Wand ist ein Kategorienfehler."""
    prop = _property(app_user)
    room = _raum(app_user, prop)
    fenster = katalog.create_template(app_user.id, {
        "kind": "OEFFNUNG", "name": "Fenster, Gattungstest",
        "default_opening_type": "FENSTER",
    })
    with pytest.raises(ValueError, match="keine Flächenvorlage"):
        raum_service.set_aufbau(app_user.id, room.id, [{
            "ref": "s1", "surface_type": "AUSSENWAND", "adjacent": "AUSSENLUFT",
            "gross_area_m2": Decimal("10.000"), "template_id": fenster.id,
        }], [])


@pytest.mark.django_db
def test_art_muss_zur_gattung_passen(app_user):
    with pytest.raises(ValueError, match="keine Öffnungsart"):
        katalog.create_template(app_user.id, {
            "kind": "FLAECHE", "name": "Widerspruch",
            "default_opening_type": "FENSTER",
        })


@pytest.mark.django_db
def test_gattung_laesst_sich_nicht_drehen(app_user):
    vorlage = _wand_vorlage(app_user, name="Dreh-Test")
    with pytest.raises(ValueError, match="lässt sich nicht ändern"):
        katalog.update_template(app_user.id, vorlage.id, {"kind": "OEFFNUNG"})


@pytest.mark.django_db
def test_doppelter_name_je_gattung(app_user):
    _wand_vorlage(app_user, name="Doppelt")
    with pytest.raises(ValueError, match="bereits eine Vorlage"):
        _wand_vorlage(app_user, name="Doppelt")
