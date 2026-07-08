"""Service-Tests der Property-Schicht gegen die echte Test-DB.

Die Test-DB wird über die Migrationskette (inkl. SQL-Baseline) aufgebaut, die
Trigger sind also scharf: Nummernvergabe per Sequenz, MERGED-Schutz,
Exclusion-Constraints. app_user-Fixture liefert der conftest.
"""
import re
from datetime import date

import pytest

from django.db import Error

from db_core.db_context import business_transaction
from db_core.models import Building, Party, Property, PropertyPartyRole, Unit
from db_core.services import identity as identity_service
from db_core.services import property as property_service


@pytest.mark.django_db
def test_create_property_legt_adresse_und_property_an(app_user):
    prop = property_service.create_property(
        app_user.id,
        name="Wohnpark Süd",
        property_type="WEG",
        street="Parkweg",
        house_number="7",
        postal_code="12345",
        city="Musterstadt",
    )
    assert prop.name == "Wohnpark Süd"
    assert prop.property_type == "WEG"
    assert prop.status == "ACTIVE"
    assert prop.version == 1
    # Nummer kommt aus der DB-Sequenz, Format OBJ-#####.
    assert re.match(r"^OBJ-[0-9]{5,}$", prop.property_number)
    # Adresse verknüpft und persistiert.
    assert prop.address.city == "Musterstadt"
    assert prop.address_id is not None


@pytest.mark.django_db
def test_create_property_nummern_sind_eindeutig(app_user):
    a = property_service.create_property(
        app_user.id, name="A", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    b = property_service.create_property(
        app_user.id, name="B", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    assert a.property_number != b.property_number


@pytest.mark.django_db
def test_create_property_ungueltiger_typ(app_user):
    with pytest.raises(ValueError):
        property_service.create_property(
            app_user.id, name="X", property_type="FALSCH",
            street="S", postal_code="1", city="C",
        )


@pytest.mark.django_db
def test_create_property_leerer_name(app_user):
    with pytest.raises(ValueError):
        property_service.create_property(
            app_user.id, name="   ", property_type="OTHER",
            street="S", postal_code="1", city="C",
        )


@pytest.mark.django_db
def test_add_building_und_unit(app_user):
    prop = property_service.create_property(
        app_user.id, name="Objekt", property_type="MIXED",
        street="S", postal_code="1", city="C",
    )
    building = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="A", name="Haupthaus",
    )
    assert Building.objects.filter(id=building.id, property_id=prop.id).exists()

    unit = property_service.add_unit(
        app_user.id, building_id=building.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="WE 1",
    )
    assert Unit.objects.filter(
        id=unit.id, building_id=building.id, property_id=prop.id
    ).exists()


@pytest.mark.django_db
def test_add_unit_ungueltiger_typ(app_user):
    prop = property_service.create_property(
        app_user.id, name="Objekt", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    building = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="A",
    )
    with pytest.raises(ValueError):
        property_service.add_unit(
            app_user.id, building_id=building.id, property_id=prop.id,
            unit_type="FALSCH", unit_number="1",
        )


@pytest.mark.django_db
def test_add_party_role(app_user):
    prop = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="S", postal_code="1", city="C",
    )
    person = identity_service.create_person(
        app_user.id, first_name="Eva", last_name="Eigner",
    )
    role = property_service.add_party_role(
        app_user.id, property_id=prop.id, party_id=person.id,
        role="PROPERTY_OWNER", valid_from=date(2020, 1, 1),
    )
    assert PropertyPartyRole.objects.filter(
        id=role.id, property_id=prop.id, party_id=person.id, role="PROPERTY_OWNER"
    ).exists()


@pytest.mark.django_db
def test_add_party_role_ungueltige_rolle(app_user):
    prop = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="S", postal_code="1", city="C",
    )
    person = identity_service.create_person(
        app_user.id, first_name="Eva", last_name="Eigner",
    )
    with pytest.raises(ValueError):
        property_service.add_party_role(
            app_user.id, property_id=prop.id, party_id=person.id,
            role="FALSCH", valid_from=date(2020, 1, 1),
        )


@pytest.mark.django_db
def test_add_party_role_merged_party_abgelehnt(app_user):
    """Der DB-Trigger trg_property_role_no_merged verbietet Referenzen auf
    zusammengeführte Parties — das muss als DB-Fehler durchschlagen."""
    prop = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="S", postal_code="1", city="C",
    )
    ziel = identity_service.create_person(
        app_user.id, first_name="Ziel", last_name="Person",
    )
    dublette = identity_service.create_person(
        app_user.id, first_name="Alt", last_name="Dublette",
    )
    with business_transaction(app_user.id):
        Party.objects.filter(id=dublette.id).update(
            status="MERGED", merged_into_party_id=ziel.id
        )
    with pytest.raises(Error):
        property_service.add_party_role(
            app_user.id, property_id=prop.id, party_id=dublette.id,
            role="OPERATOR", valid_from=date(2020, 1, 1),
        )
