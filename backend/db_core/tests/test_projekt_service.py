"""Service-Tests der Projekt-Schicht gegen die echte Test-DB.

Nummernvergabe (P-…/V-…) und Statusautomat-Trigger sind scharf. app_user aus
conftest; Liegenschaften/Parties über die bestehenden Services.
"""
import re

import pytest

from db_core.models import Project, ProjectProperty, ServiceCase, StatusChange
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
    assert re.match(r"^P-[0-9]{2}-[0-9]{4,}$", p.project_number)


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
    assert re.match(r"^V-[0-9]{2}-[0-9]{4,}$", case.case_number)


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


# --- Vorgangs-Statuswechsel ------------------------------------------------

def _neuer_vorgang(app_user):
    obj = _property(app_user, "Statusobjekt")
    return projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Statusvorgang"
    )


@pytest.mark.django_db
def test_transitions_von_neu_aus_der_tabelle(app_user):
    """Erlaubte Übergänge kommen aus workflow.status_transition (nicht hartkodiert),
    Labels aus status_catalog, sortiert nach sort_order."""
    trans = projekt_service.service_case_transitions("NEU")
    ziele = {t["to_status"] for t in trans}
    assert ziele == {"IN_PRUEFUNG", "ABGELEHNT"}
    by = {t["to_status"]: t for t in trans}
    assert by["IN_PRUEFUNG"]["reason_required"] is False
    assert by["IN_PRUEFUNG"]["label"] == "In Prüfung"
    assert by["IN_PRUEFUNG"]["recht"] == "AENDERN"
    assert by["ABGELEHNT"]["reason_required"] is True
    # sort_order: IN_PRUEFUNG (2) vor ABGELEHNT (7)
    assert [t["to_status"] for t in trans] == ["IN_PRUEFUNG", "ABGELEHNT"]


@pytest.mark.django_db
def test_transitions_freigabe_verlangt_freigeben_recht(app_user):
    """FREIGABE_AUSSTEHEND → BEAUFTRAGT ist die Beauftragung (Freigabetor)."""
    trans = projekt_service.service_case_transitions("FREIGABE_AUSSTEHEND")
    by = {t["to_status"]: t for t in trans}
    assert by["BEAUFTRAGT"]["recht"] == "FREIGEBEN"
    # Rücksprung ist begründungspflichtig und nur AENDERN
    assert by["IN_PRUEFUNG"]["recht"] == "AENDERN"
    assert by["IN_PRUEFUNG"]["reason_required"] is True


@pytest.mark.django_db
def test_advance_gueltiger_uebergang(app_user):
    case = _neuer_vorgang(app_user)
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=case.id, to_status="IN_PRUEFUNG"
    )
    case.refresh_from_db()
    assert case.status == "IN_PRUEFUNG"
    changes = StatusChange.objects.filter(entity="service_case", entity_id=case.id)
    assert changes.filter(from_status="NEU", to_status="IN_PRUEFUNG").exists()


@pytest.mark.django_db
def test_advance_begruendungspflichtig_ohne_grund(app_user):
    case = _neuer_vorgang(app_user)
    with pytest.raises(ValueError, match="Begründung"):
        projekt_service.advance_service_case_status(
            app_user.id, service_case_id=case.id, to_status="ABGELEHNT"
        )
    case.refresh_from_db()
    assert case.status == "NEU"


@pytest.mark.django_db
def test_advance_begruendungspflichtig_mit_grund(app_user):
    case = _neuer_vorgang(app_user)
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=case.id, to_status="ABGELEHNT",
        reason="Kein Mandat",
    )
    case.refresh_from_db()
    assert case.status == "ABGELEHNT"
    assert StatusChange.objects.filter(
        entity="service_case", entity_id=case.id, to_status="ABGELEHNT",
        reason="Kein Mandat",
    ).exists()


@pytest.mark.django_db
def test_advance_ungueltiger_uebergang(app_user):
    case = _neuer_vorgang(app_user)
    with pytest.raises(ValueError, match="nicht erlaubt"):
        projekt_service.advance_service_case_status(
            app_user.id, service_case_id=case.id, to_status="BEAUFTRAGT"
        )
    case.refresh_from_db()
    assert case.status == "NEU"


@pytest.mark.django_db
def test_advance_unbekannter_vorgang(app_user):
    import uuid as _uuid

    with pytest.raises(ValueError, match="nicht gefunden"):
        projekt_service.advance_service_case_status(
            app_user.id, service_case_id=_uuid.uuid4(), to_status="IN_PRUEFUNG"
        )
