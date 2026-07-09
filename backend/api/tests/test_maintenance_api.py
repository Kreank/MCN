"""API-Tests der Wartungs-Endpoints (Wartungsverträge) über den Django-Test-Client.
Read-only; Setup baut über den Service einen aktiven Vertrag mit fälliger erster
Wartung und einer ausgelösten Aktion.
"""
from datetime import date
from uuid import uuid4

import pytest

from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import wartung as wartung_service


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Wartungshaus", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Karl", last_name="Kunde"
    )
    c = wartung_service.create_contract(
        app_user.id, property_id=obj.id, name="Aufzugsprüfung",
        start_date=date(2026, 6, 1), interval_kind="JAEHRLICH",
        due_action="AUFGABE", party_id=kunde.id, lead_time_days=14,
        notes="Jährliche Prüfung.",
    )
    wartung_service.trigger_action(app_user.id, contract_id=c.id, note="Erste Prüfung")
    c.refresh_from_db()
    return {"obj": obj, "contract": c}


@pytest.mark.django_db
def test_liste(admin_client, seeded):
    r = admin_client.get("/api/maintenance/contracts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    it = body["items"][0]
    assert it["contract_number"].startswith("W-")
    assert it["name"] == "Aufzugsprüfung"
    assert it["customer"] == "Karl Kunde"
    assert it["property"]["name"] == "Wartungshaus"
    assert it["due_action"] == "AUFGABE"


@pytest.mark.django_db
def test_statusfilter(admin_client, seeded, app_user):
    wartung_service.set_status(
        app_user.id, contract_id=seeded["contract"].id, to_status="INAKTIV"
    )
    assert admin_client.get("/api/maintenance/contracts?status=AKTIV").json()["total"] == 0
    assert admin_client.get("/api/maintenance/contracts?status=INAKTIV").json()["total"] == 1


@pytest.mark.django_db
def test_faelligkeitsfilter(admin_client, seeded):
    # next_due wurde durch das Auslösen auf 2027-06-01 vorgerückt → nicht fällig.
    assert admin_client.get("/api/maintenance/contracts?due=true").json()["total"] == 0


@pytest.mark.django_db
def test_unbekannter_status_422(admin_client, seeded):
    r = admin_client.get("/api/maintenance/contracts?status=QUATSCH")
    assert r.status_code == 422


@pytest.mark.django_db
def test_detail_mit_historie(admin_client, seeded):
    r = admin_client.get(f"/api/maintenance/contracts/{seeded['contract'].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["notes"] == "Jährliche Prüfung."
    assert body["next_due_date"] == "2027-06-01"
    assert len(body["events"]) == 1
    ev = body["events"][0]
    assert ev["action"] == "AUFGABE"
    assert ev["result_object_type"] == "workflow.task"
    assert ev["triggered_by"] == "Test Sachbearbeiter"


@pytest.mark.django_db
def test_detail_404(admin_client, db):
    r = admin_client.get(f"/api/maintenance/contracts/{uuid4()}")
    assert r.status_code == 404
