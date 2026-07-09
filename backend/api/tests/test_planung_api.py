"""API-Tests der Planungs-Endpoints (Einsätze) über den Django-Test-Client.

Read-only: Liste, Filter, Detail, 404. Setup baut über die Services einen bis
IN_AUSFUEHRUNG geschalteten Auftrag und darauf zwei Einsätze (einer vor Ort mit
Zuweisung/Zeit/Material, einer nur geplant).
"""
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Einsatzhaus", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    principal = identity_service.create_person(
        app_user.id, first_name="Petra", last_name="Prinzipal"
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Sockelrisse setzen"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        app_user.id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status=to_status
        )

    # Einsatz 1: vor Ort, mit Zuweisung, Zeit und Material.
    j1 = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=T0, scheduled_end=T1,
        on_site_contact_party_id=principal.id,
        access_instructions="Schlüssel Hausmeister.",
    )
    for to_status in ("GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"):
        einsatz_service.advance_status(
            app_user.id, service_job_id=j1.id, to_status=to_status
        )
    einsatz_service.assign_user(
        app_user.id, service_job_id=j1.id, assignee_user_id=app_user.id, role="LEAD"
    )
    einsatz_service.log_time(
        app_user.id, service_job_id=j1.id, user_id=app_user.id,
        time_type="ARBEITSZEIT", started_at=T0, ended_at=T1,
    )
    einsatz_service.log_material(
        app_user.id, service_job_id=j1.id,
        description="Injektionsharz", quantity=Decimal("3.5"), unit="kg",
        recorded_by=app_user.id,
    )

    # Einsatz 2: nur geplant.
    j2 = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0, scheduled_end=T1,
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=j2.id, to_status="GEPLANT"
    )
    return {"order": order, "j1": j1, "j2": j2}


@pytest.mark.django_db
def test_liste_und_pagination(admin_client, seeded):
    r = admin_client.get("/api/planung/einsaetze?page=1&page_size=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    # Jeder Eintrag trägt den Auftragstitel und die Objekt-Referenz.
    it = body["items"][0]
    assert it["work_order"]["title"] == "Sockelrisse setzen"
    assert it["property"]["name"] == "Einsatzhaus"


@pytest.mark.django_db
def test_statusfilter(admin_client, seeded):
    r = admin_client.get("/api/planung/einsaetze?status=VOR_ORT")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "VOR_ORT"
    assert body["items"][0]["assignee_count"] == 1


@pytest.mark.django_db
def test_unbekannter_status_422(admin_client, seeded):
    r = admin_client.get("/api/planung/einsaetze?status=QUATSCH")
    assert r.status_code == 422


@pytest.mark.django_db
def test_auftragsfilter(admin_client, seeded):
    r = admin_client.get(f"/api/planung/einsaetze?work_order_id={seeded['order'].id}")
    assert r.json()["total"] == 2


@pytest.mark.django_db
def test_detail_mit_zuweisung_zeit_material(admin_client, seeded):
    r = admin_client.get(f"/api/planung/einsaetze/{seeded['j1'].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "VOR_ORT"
    assert body["job_number"].startswith("E-")
    assert body["on_site_contact"] == "Petra Prinzipal"
    assert body["access_instructions"] == "Schlüssel Hausmeister."
    assert [a["role"] for a in body["assignments"]] == ["LEAD"]
    assert body["assignments"][0]["display_name"] == "Test Sachbearbeiter"
    assert {t["time_type"] for t in body["time_entries"]} == {"ARBEITSZEIT"}
    assert body["material_entries"][0]["description"] == "Injektionsharz"
    # Vollständiger Statusverlauf UNGEPLANT→…→VOR_ORT. Die Reihenfolge lässt sich
    # hier nicht prüfen: alle Wechsel laufen in EINER pytest-Transaktion, daher
    # liefert now() (Transaktionsstartzeit) für jede Zeile denselben occurred_at
    # → Gleichstand. In der echten App (separate Transaktionen) ist die Sortierung
    # eindeutig absteigend.
    assert {h["to_status"] for h in body["history"]} == {
        "UNGEPLANT", "GEPLANT", "BESTAETIGT", "UNTERWEGS", "VOR_ORT"
    }


@pytest.mark.django_db
def test_detail_404(admin_client, db):
    r = admin_client.get(f"/api/planung/einsaetze/{uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_plantafel(admin_client, seeded):
    # Beide Einsätze sind auf 2026-07-13 geplant; j1 hat eine Zuweisung, j2 nicht.
    r = admin_client.get("/api/planung/plantafel?date_from=2026-07-13&date_to=2026-07-13")
    assert r.status_code == 200
    body = r.json()
    assert len(body["jobs"]) == 2
    assert [res["display_name"] for res in body["resources"]] == ["Test Sachbearbeiter"]
    assert body["unassigned_count"] == 1
    vor_ort = next(j for j in body["jobs"] if j["status"] == "VOR_ORT")
    assert len(vor_ort["assignee_ids"]) == 1
    assert vor_ort["title"] == "Sockelrisse setzen"


@pytest.mark.django_db
def test_plantafel_range_invalid(admin_client, db):
    r = admin_client.get("/api/planung/plantafel?date_from=2026-07-20&date_to=2026-07-10")
    assert r.status_code == 422


@pytest.mark.django_db
def test_plantafel_range_zu_gross(admin_client, db):
    r = admin_client.get("/api/planung/plantafel?date_from=2026-01-01&date_to=2026-12-31")
    assert r.status_code == 422
