"""Tests der Identity-Service-Schicht gegen die echte DB (Trigger scharf)."""
import datetime

import pytest

from db_core.models import Organization, Party, Person
from db_core.services import identity as identity_service


@pytest.mark.django_db
def test_create_person_legt_party_und_person_an(app_user):
    party = identity_service.create_person(
        app_user.id,
        first_name="Sabine",
        last_name="Krüger",
        salutation="Frau",
        birth_date=datetime.date(1980, 5, 1),
    )
    assert party.party_type == "PERSON"
    assert party.display_name == "Sabine Krüger"
    assert party.status == "ACTIVE"
    person = Person.objects.get(party_id=party.id)
    assert person.first_name == "Sabine"
    assert person.last_name == "Krüger"
    # DB-Default hat die Zeitstempel gesetzt
    assert Party.objects.get(id=party.id).created_at is not None


@pytest.mark.django_db
def test_create_organization_legt_party_und_org_an(app_user):
    party = identity_service.create_organization(
        app_user.id,
        legal_name="Elektro Schneider GmbH",
        organization_type="COMPANY",
        legal_form="GmbH",
    )
    assert party.party_type == "ORGANIZATION"
    assert party.display_name == "Elektro Schneider GmbH"
    org = Organization.objects.get(party_id=party.id)
    assert org.organization_type == "COMPANY"


@pytest.mark.django_db
def test_create_organization_display_name_override(app_user):
    party = identity_service.create_organization(
        app_user.id,
        legal_name="WEG Lindenstraße 12, Musterstadt",
        organization_type="WEG",
        display_name="WEG Lindenstraße 12",
    )
    assert party.display_name == "WEG Lindenstraße 12"


@pytest.mark.django_db
def test_create_organization_ungueltiger_typ(app_user):
    with pytest.raises(ValueError):
        identity_service.create_organization(
            app_user.id,
            legal_name="Irgendwas",
            organization_type="NICHT_EXISTENT",
        )
    # kein Torso: keine Party angelegt
    assert not Party.objects.filter(display_name="Irgendwas").exists()


@pytest.mark.django_db
def test_create_person_ohne_app_user_id(db):
    with pytest.raises(ValueError):
        identity_service.create_person(None, first_name="X", last_name="Y")
