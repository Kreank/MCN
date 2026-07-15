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


@pytest.mark.django_db
@pytest.mark.parametrize(
    "first_name,last_name", [("  ", "Meyer"), ("Max", "   "), ("", "")]
)
def test_create_person_leerer_name_ist_valuefehler(app_user, first_name, last_name):
    """Leere/nur-Leerzeichen-Namen sind ein Fachfehler (ValueError → 422), kein
    roher DB-IntegrityError (500): display_name trägt den CHECK btrim(...) <> ''."""
    vorher = Party.objects.count()
    with pytest.raises(ValueError):
        identity_service.create_person(
            app_user.id, first_name=first_name, last_name=last_name
        )
    assert Party.objects.count() == vorher


# --- Ansprechpartner --------------------------------------------------------

@pytest.mark.django_db
def test_add_contact_person_bestehend(app_user):
    org = identity_service.create_organization(
        app_user.id, legal_name="Meyer GmbH", organization_type="COMPANY",
    )
    person = identity_service.create_person(
        app_user.id, first_name="Klaus", last_name="Meyer",
    )
    rel = identity_service.add_contact_person(
        app_user.id, org.id, person_party_id=person.id,
    )
    assert rel.from_party_id == person.id
    assert rel.to_party_id == org.id
    assert rel.relationship_type == "CONTACT_PERSON_FOR"
    liste = identity_service.list_contact_persons(org.id)
    assert [r.from_party_id for r in liste] == [person.id]


@pytest.mark.django_db
def test_add_contact_person_neu_in_einem_vorgang(app_user):
    org = identity_service.create_organization(
        app_user.id, legal_name="Bau AG", organization_type="COMPANY",
    )
    rel = identity_service.add_contact_person(
        app_user.id, org.id,
        new_person={"first_name": "Neu", "last_name": "Ansprech"},
    )
    person = Person.objects.get(party_id=rel.from_party_id)
    assert person.last_name == "Ansprech"
    assert Party.objects.get(id=rel.from_party_id).party_type == "PERSON"


@pytest.mark.django_db
def test_add_contact_person_nur_an_organisation(app_user):
    p1 = identity_service.create_person(app_user.id, first_name="A", last_name="B")
    p2 = identity_service.create_person(app_user.id, first_name="C", last_name="D")
    with pytest.raises(ValueError):
        identity_service.add_contact_person(app_user.id, p1.id, person_party_id=p2.id)


@pytest.mark.django_db
def test_add_contact_person_unbekannte_person(app_user):
    import uuid as _uuid
    org = identity_service.create_organization(
        app_user.id, legal_name="X GmbH", organization_type="COMPANY",
    )
    with pytest.raises(ValueError):
        identity_service.add_contact_person(
            app_user.id, org.id, person_party_id=_uuid.uuid4(),
        )


@pytest.mark.django_db
def test_add_contact_person_dublette_422(app_user):
    org = identity_service.create_organization(
        app_user.id, legal_name="Y GmbH", organization_type="COMPANY",
    )
    person = identity_service.create_person(app_user.id, first_name="E", last_name="F")
    identity_service.add_contact_person(app_user.id, org.id, person_party_id=person.id)
    with pytest.raises(ValueError):
        identity_service.add_contact_person(
            app_user.id, org.id, person_party_id=person.id,
        )


@pytest.mark.django_db
def test_remove_contact_person_beendet_statt_loeschen(app_user):
    org = identity_service.create_organization(
        app_user.id, legal_name="Z GmbH", organization_type="COMPANY",
    )
    person = identity_service.create_person(app_user.id, first_name="G", last_name="H")
    rel = identity_service.add_contact_person(
        app_user.id, org.id, person_party_id=person.id,
        valid_from=datetime.date(2020, 1, 1),
    )
    ended = identity_service.remove_contact_person(app_user.id, rel.id)
    assert ended.valid_until is not None
    # Nicht mehr in der aktiven Liste
    assert identity_service.list_contact_persons(org.id) == []


# --- Vorgangszählung je Ansprechpartner (Kontakte-8) ------------------------

@pytest.mark.django_db
def test_contact_person_case_counts_zaehlt_gemeldete_vorgaenge(app_user):
    """Die per-Person aggregierte Anzahl gemeldeter Vorgänge (Melderrolle).

    Fachkante: service_case.reported_by_party_id. Zwei Vorgänge für Person A,
    keiner für Person B → {A: 2}; B fehlt (der Aufrufer setzt 0).
    """
    from db_core.services import projekt as projekt_service
    from db_core.services import property as property_service

    obj = property_service.create_property(
        app_user.id, name="Zählobjekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    melder = identity_service.create_person(app_user.id, first_name="Melo", last_name="Melder")
    ohne = identity_service.create_person(app_user.id, first_name="Ohne", last_name="Vorgang")
    for betreff in ("Heizung", "Aufzug"):
        projekt_service.create_service_case(
            app_user.id, property_id=obj.id, subject=betreff,
            reported_by_party_id=melder.id,
        )

    counts = identity_service.contact_person_case_counts([melder.id, ohne.id])
    assert counts.get(melder.id) == 2
    assert ohne.id not in counts


@pytest.mark.django_db
def test_contact_person_case_counts_leere_eingabe():
    assert identity_service.contact_person_case_counts([]) == {}
    assert identity_service.contact_person_case_counts([None]) == {}


@pytest.mark.django_db
def test_contact_person_case_counts_eigene_ohne_akteur_leer(app_user):
    """Scope 'EIGENE' ohne Akteur → leer (fail-closed), auch bei echten Vorgängen."""
    from db_core.services import projekt as projekt_service
    from db_core.services import property as property_service

    obj = property_service.create_property(
        app_user.id, name="Objekt2", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    melder = identity_service.create_person(app_user.id, first_name="M", last_name="M")
    projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="X", reported_by_party_id=melder.id,
    )
    assert identity_service.contact_person_case_counts(
        [melder.id], scope="EIGENE", actor_id=None
    ) == {}


# --- Adressen ---------------------------------------------------------------

@pytest.mark.django_db
def test_add_address_mit_typ(app_user):
    party = identity_service.create_person(app_user.id, first_name="I", last_name="J")
    link = identity_service.add_address(
        app_user.id, party.id, address_type="BUSINESS",
        street="Hauptstr.", house_number="1", postal_code="80331", city="München",
    )
    assert link.address_type == "BUSINESS"
    liste = identity_service.list_addresses(party.id)
    assert len(liste) == 1
    assert liste[0].address.city == "München"


@pytest.mark.django_db
def test_add_address_ungueltiger_typ(app_user):
    party = identity_service.create_person(app_user.id, first_name="K", last_name="L")
    with pytest.raises(ValueError):
        identity_service.add_address(
            app_user.id, party.id, address_type="FALSCH",
            street="X", postal_code="1", city="Y",
        )


@pytest.mark.django_db
def test_add_address_exklusivitaet_primaer_422(app_user):
    party = identity_service.create_person(app_user.id, first_name="M", last_name="N")
    identity_service.add_address(
        app_user.id, party.id, address_type="BILLING",
        street="A", postal_code="1", city="B", is_primary=True,
        valid_from=datetime.date(2020, 1, 1),
    )
    # Zweite primäre BILLING-Adresse im überlappenden Zeitraum → 422
    with pytest.raises(ValueError):
        identity_service.add_address(
            app_user.id, party.id, address_type="BILLING",
            street="C", postal_code="2", city="D", is_primary=True,
            valid_from=datetime.date(2020, 6, 1),
        )


# --- Kommunikationswege -----------------------------------------------------

@pytest.mark.django_db
def test_add_contact_point(app_user):
    party = identity_service.create_person(app_user.id, first_name="O", last_name="P")
    point = identity_service.add_contact_point(
        app_user.id, party.id, contact_type="EMAIL", value="o.p@example.test",
    )
    assert point.contact_type == "EMAIL"
    assert identity_service.list_contact_points(party.id)[0].value == "o.p@example.test"


@pytest.mark.django_db
def test_add_contact_point_ungueltiger_typ(app_user):
    party = identity_service.create_person(app_user.id, first_name="Q", last_name="R")
    with pytest.raises(ValueError):
        identity_service.add_contact_point(
            app_user.id, party.id, contact_type="TELEPATHIE", value="x",
        )


@pytest.mark.django_db
def test_deactivate_contact_point(app_user):
    party = identity_service.create_person(app_user.id, first_name="S", last_name="T")
    point = identity_service.add_contact_point(
        app_user.id, party.id, contact_type="PHONE", value="+49 89 1",
        valid_from=datetime.date(2020, 1, 1),
    )
    ended = identity_service.deactivate_contact_point(app_user.id, point.id)
    assert ended.valid_until is not None
    assert identity_service.list_contact_points(party.id) == []
