"""Der Steckbrief bleibt bei **konstanter** Abfragezahl — egal wie viele Zeilen.

Das ist die einzige Eigenschaft dieses Moduls, die ein fachlicher Test nicht
sieht: Ein N+1 liefert **richtige** Werte. Er kostet nur je Zeile fünf weitere
Roundtrips — bei 25 Zeilen im Picker und einer Suche, die bei jedem Tastendruck
feuert, ist das der Unterschied zwischen flüssig und unbenutzbar. Deshalb zählt
dieser Test die Abfragen, nicht die Felder.
"""
from datetime import date

import pytest

from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import property_steckbrief, verwaltung as verwaltung_service


def _objekt(actor, nummer):
    """Eine Liegenschaft mit Eigentümer, Verwaltung, Gebäude und Einheit."""
    eigentuemer = identity_service.create_organization(
        actor, legal_name=f"WEG Nr. {nummer}", organization_type="WEG")
    verwalter = identity_service.create_organization(
        actor, legal_name=f"Verwaltung {nummer}",
        organization_type="PROPERTY_MANAGEMENT")
    kontakt = identity_service.create_person(
        actor, first_name="Kontakt", last_name=f"Nr{nummer}")
    identity_service.add_contact_point(
        actor, kontakt.id, contact_type="PHONE", value=f"030 {nummer}00",
        is_primary=True)
    prop = property_service.create_property(
        actor, name=f"Objekt {nummer}", property_type="WEG",
        street="Teststraße", house_number=str(nummer),
        postal_code="10115", city="Berlin")
    property_service.add_party_role(
        actor, property_id=prop.id, party_id=eigentuemer.id,
        role="COMMUNITY_OF_OWNERS", valid_from=date(2020, 1, 1))
    gebaeude = property_service.add_building(
        actor, property_id=prop.id, building_number="A")
    property_service.add_unit(
        actor, building_id=gebaeude.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="WE 1")
    verwaltung_service.create_mandat(
        actor, property_id=prop.id,
        management_party_id=verwalter.id,
        principal_party_id=eigentuemer.id,
        default_contact_party_id=kontakt.id,
        mandate_type="WEG_MANAGEMENT", scope_type="ENTIRE_PROPERTY",
        valid_from=date(2021, 1, 1))
    return prop


#: Die Abfragen aus `property_steckbrief.steckbriefe` (siehe Modul-Docstring):
#: Liegenschaften, Rollen, Mandate, Einheitenzahl, Gebäude, Telefonnummern.
ERWARTETE_ABFRAGEN = 6


@pytest.mark.django_db
def test_abfragezahl_bleibt_konstant(app_user, django_assert_num_queries):
    eins = [_objekt(app_user.id, 1)]
    with django_assert_num_queries(ERWARTETE_ABFRAGEN):
        property_steckbrief.steckbriefe([p.id for p in eins])

    viele = eins + [_objekt(app_user.id, n) for n in range(2, 11)]
    with django_assert_num_queries(ERWARTETE_ABFRAGEN):
        briefe = property_steckbrief.steckbriefe([p.id for p in viele])

    assert len(briefe) == 10
    for p in viele:
        s = briefe[p.id]
        assert s.verwaltung is not None
        assert s.einheiten_anzahl == 1
        assert s.telefon is not None


@pytest.mark.django_db
def test_leere_eingabe_fragt_gar_nicht(db, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert property_steckbrief.steckbriefe([]) == {}


@pytest.mark.django_db
def test_mandat_vor_beginn_gilt_noch_nicht(app_user):
    """Vor dem Mandatsbeginn gibt es keine Verwaltung — die Eigentümerrolle schon."""
    prop = _objekt(app_user.id, 42)
    brief = property_steckbrief.steckbriefe([prop.id], stichtag=date(2020, 6, 1))[prop.id]
    # Stichtag VOR Mandatsbeginn (2021-01-01), aber NACH Rollenbeginn (2020-01-01).
    assert brief.verwaltung is None
    assert brief.eigentuemer == ["WEG Nr. 42"]


@pytest.mark.django_db
def test_beendetes_mandat_gilt_nicht_mehr(app_user):
    """status='ENDED' schließt aus — auch wenn das Enddatum noch in der Zukunft liegt.

    Das ist der Fall, für den `.exclude(status="ENDED")` in `steckbriefe` steht und
    den ein reiner Stichtagstest **nicht** trifft: Wer ein Mandat zum Jahresende
    kündigt, setzt Status und `valid_until` heute (der CHECK verlangt beides
    zusammen). Das Zeitfenster ist dann noch offen, die Verwaltung aber gekündigt —
    und die gekündigte Verwaltung anzurufen ist die falsche Auskunft.
    """
    prop = _objekt(app_user.id, 43)
    mandat = verwaltung_service.mandate_der_liegenschaft(prop.id)[0]
    ende = date(2099, 12, 31)
    verwaltung_service.end_mandat(app_user.id, mandat.id, valid_until=ende)

    stichtag = date(2026, 6, 1)
    # Gegenprobe: Das Zeitfenster [valid_from, valid_until) umfasst den Stichtag —
    # ohne den Statusausschluss käme das Mandat also durch.
    assert mandat.valid_from <= stichtag < ende

    brief = property_steckbrief.steckbriefe([prop.id], stichtag=stichtag)[prop.id]
    assert brief.verwaltung is None
    # Der Eigentümer bleibt — beendet ist das Mandat, nicht die Liegenschaft.
    assert brief.eigentuemer == ["WEG Nr. 43"]


@pytest.mark.django_db
def test_rolle_endet_am_stichtag_gilt_am_stichtag_nicht_mehr(app_user):
    """Halboffen `[valid_from, valid_until)`: `valid_until = heute` gilt heute NICHT.

    Dieselbe Grenze wie `api/property.py::_is_current` (`valid_until > today`).
    Ein Eigentümerwechsel wird auf den Tag der Übergabe datiert; würde die obere
    Grenze mitzählen, stünden am Übergabetag **beide** Eigentümer im Steckbrief.
    """
    prop = _objekt(app_user.id, 44)
    voreigentuemer = identity_service.create_organization(
        app_user.id, legal_name="Voreigentümerin GmbH", organization_type="COMPANY")
    stichtag = date(2026, 6, 1)
    property_service.add_party_role(
        app_user.id, property_id=prop.id, party_id=voreigentuemer.id,
        role="PROPERTY_OWNER", valid_from=date(2019, 1, 1), valid_until=stichtag)

    brief = property_steckbrief.steckbriefe([prop.id], stichtag=stichtag)[prop.id]
    assert brief.eigentuemer == ["WEG Nr. 44"]

    # Einen Tag vorher galt sie noch — sonst prüfte der Test nur eine leere Rolle.
    davor = property_steckbrief.steckbriefe(
        [prop.id], stichtag=date(2026, 5, 31))[prop.id]
    assert "Voreigentümerin GmbH" in davor.eigentuemer
