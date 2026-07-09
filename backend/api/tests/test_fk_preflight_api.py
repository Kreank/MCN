"""API-Tests: unbekannte Payload-/Pfad-Fremdschlüssel enden als 422, nicht 500.

Belegt end-to-end, dass die Service-Vorabprüfung über die Router-Schicht als
sauberer 422 (Fachfehler) beim Aufrufer landet — nicht als 500 (IntegrityError).
"""
import uuid

import pytest


@pytest.mark.django_db
def test_create_task_unbekannte_party_422(admin_client):
    """Payload-FK (party_id) unbekannt → 422 (nicht 500)."""
    r = admin_client.post(
        "/api/workflow/tasks",
        data={"title": "Mit Geist-Kontakt", "party_id": str(uuid.uuid4())},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_record_payment_unbekannte_rechnung_kein_500(admin_client):
    """Pfad-FK (invoice_id) unbekannt → 422 statt 500 (kein IntegrityError)."""
    r = admin_client.post(
        f"/api/buchhaltung/invoices/{uuid.uuid4()}/payments",
        data={"amount": "10.00", "paid_at": "2026-01-01"},
        content_type="application/json",
    )
    assert r.status_code != 500, r.content
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_create_service_case_unbekannte_liegenschaft_422(admin_client):
    """Payload-FK (property_id) unbekannt → 422."""
    from db_core.models import Project

    # Projekt aus dem Pfad muss existieren; die Liegenschaft im Payload nicht.
    from db_core.services import projekt as projekt_service

    project = projekt_service.create_project(
        _actor(admin_client), name="Projekt für Vorgang"
    )
    r = admin_client.post(
        f"/api/workflow/projects/{project.id}/service_cases",
        data={"property_id": str(uuid.uuid4()), "subject": "Geist-Objekt"},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert Project.objects.filter(id=project.id).exists()


def _actor(client):
    """Die app_user-ID des eingeloggten Test-Clients (ADMINISTRATION)."""
    from django.contrib.auth import get_user_model

    user_id = client.session["_auth_user_id"]
    return get_user_model().objects.get(pk=user_id).app_user_id
