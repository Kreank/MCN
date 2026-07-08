"""Service-Tests der Projekt-Schicht gegen die echte Test-DB.

Nummernvergabe (P-…/V-…) und Statusautomat-Trigger sind scharf. app_user aus
conftest; Liegenschaften/Parties über die bestehenden Services.
"""
import re

import pytest

from db_core.models import Project, ProjectProperty, ServiceCase
from db_core.services import identity as identity_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service


def _property(app_user, name="Objekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


@pytest.mark.django_db
def test_create_project_basis(app_user):
    p = projekt_service.create_project(app_user.id, name="Dachsanierung Nord")
    assert p.name == "Dachsanierung Nord"
    assert p.status == "OPEN"
    assert p.version == 1
    assert re.match(r"^P-[0-9]{4}-[0-9]{6,}$", p.project_number)


@pytest.mark.django_db
def test_create_project_mit_liegenschaften(app_user):
    obj1 = _property(app_user, "Haus A")
    obj2 = _property(app_user, "Haus B")
    p = projekt_service.create_project(
        app_user.id, name="Mehrobjekt", property_ids=[obj1.id, obj2.id]
    )
    links = ProjectProperty.objects.filter(project_id=p.id)
    assert links.count() == 2
    assert {l.property_id for l in links} == {obj1.id, obj2.id}


@pytest.mark.django_db
def test_create_project_leerer_name(app_user):
    with pytest.raises(ValueError):
        projekt_service.create_project(app_user.id, name="   ")


@pytest.mark.django_db
def test_create_service_case_startet_neu(app_user):
    obj = _property(app_user)
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Heizung defekt"
    )
    assert case.subject == "Heizung defekt"
    assert case.status == "NEU"
    assert case.responsibility_scope == "UNKNOWN"
    assert re.match(r"^V-[0-9]{4}-[0-9]{6,}$", case.case_number)


@pytest.mark.django_db
def test_service_case_am_projekt(app_user):
    obj = _property(app_user)
    p = projekt_service.create_project(app_user.id, name="Projekt X")
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Rohrbruch", project_id=p.id,
    )
    assert ServiceCase.objects.filter(id=case.id, project_id=p.id).exists()


@pytest.mark.django_db
def test_create_service_case_ungueltige_prioritaet(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        projekt_service.create_service_case(
            app_user.id, property_id=obj.id, subject="X", priority="FALSCH"
        )


@pytest.mark.django_db
def test_create_service_case_mit_melder(app_user):
    obj = _property(app_user)
    melder = identity_service.create_person(
        app_user.id, first_name="Max", last_name="Melder"
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Meldung",
        reported_by_party_id=melder.id,
    )
    assert ServiceCase.objects.get(id=case.id).reported_by_party_id == melder.id
