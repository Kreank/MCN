"""Durchsetzung der Zeilenbegrenzung row_scope='EIGENE' (Beschluss B-37).

Die Startmatrix (Migration 0026) gibt der Rolle MONTEUR im Modul workflow nur
'EIGENE': er darf ausschließlich seine eigenen Aufgaben/Einsätze sehen und
bearbeiten. Geprüft wird das Verhalten der Endpunkte, die den Scope auswerten
(`require_scoped`), sowie die fail-closed-Haltung der übrigen (`require` → 403).

Gegenprobe: ADMINISTRATION ('ALLE') sieht weiterhin alles — keine Regression.
"""
import uuid
from datetime import datetime, timezone as dt_timezone

import pytest
from django.test import Client

from db_core.models import AppUser
from db_core.services import aufgabe as aufgabe_service
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

from .conftest import make_app_user, make_role_user

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 13, 12, 0, tzinfo=dt_timezone.utc)


def _monteur_client():
    """Eingeloggter MONTEUR-Client + sein app_user (für Zuweisungen)."""
    user, app_user = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)
    return client, app_user


def _admin_client():
    user, app_user = make_role_user("ADMINISTRATION")
    client = Client()
    client.force_login(user)
    return client, app_user


# --- Aufgaben (workflow.task) ----------------------------------------------

@pytest.mark.django_db
def test_monteur_sieht_nur_eigene_aufgaben():
    creator = make_app_user("Dispo")
    client, monteur = _monteur_client()
    fremd = make_app_user("Fremder Monteur")

    aufgabe_service.create_task(
        creator.id, title="Meine Aufgabe", assigned_to_user_id=monteur.id
    )
    aufgabe_service.create_task(
        creator.id, title="Fremde Aufgabe", assigned_to_user_id=fremd.id
    )
    aufgabe_service.create_task(creator.id, title="Niemandem zugewiesen")

    r = client.get("/api/workflow/tasks")
    assert r.status_code == 200, r.content
    titles = {i["title"] for i in r.json()["items"]}
    assert titles == {"Meine Aufgabe"}


@pytest.mark.django_db
def test_admin_sieht_alle_aufgaben():
    creator = make_app_user("Dispo")
    client, _admin = _admin_client()
    fremd = make_app_user("Fremder Monteur")
    aufgabe_service.create_task(
        creator.id, title="A-Aufgabe", assigned_to_user_id=fremd.id
    )
    aufgabe_service.create_task(creator.id, title="B-Aufgabe")

    r = client.get("/api/workflow/tasks")
    assert r.status_code == 200
    titles = {i["title"] for i in r.json()["items"]}
    assert {"A-Aufgabe", "B-Aufgabe"} <= titles


@pytest.mark.django_db
def test_monteur_darf_aufgabe_anlegen():
    """Ohne Zuweisung wird der Akteur selbst zum Empfänger (Scope EIGENE)."""
    client, monteur = _monteur_client()
    r = client.post(
        "/api/workflow/tasks",
        data={"title": "Vom Monteur angelegt"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["title"] == "Vom Monteur angelegt"
    assert r.json()["assigned_to"]["id"] == str(monteur.id)
    # …und er sieht sie danach auch: keine Zeile außerhalb des eigenen Sichtfelds.
    liste = client.get("/api/workflow/tasks").json()
    assert liste["total"] == 1


@pytest.mark.django_db
def test_monteur_kann_aufgabe_nicht_fremd_zuweisen():
    """Regression: `require_create` erlaubte es, eine Aufgabe auf die Liste eines
    Kollegen zu legen — die der Monteur danach selbst nicht mehr sah."""
    fremd = make_app_user("Fremde Person")
    client, _monteur = _monteur_client()
    r = client.post(
        "/api/workflow/tasks",
        data={"title": "Fremdzuweisung", "assigned_to_user_id": str(fremd.id)},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content
    assert "andere" in r.json()["detail"].lower()


@pytest.mark.django_db
def test_administration_darf_weiterhin_fremd_zuweisen():
    """Gegenprobe: Scope ALLE bleibt unbeschränkt."""
    fremd = make_app_user("Empfänger")
    client, _admin = _admin_client()
    r = client.post(
        "/api/workflow/tasks",
        data={"title": "Delegiert", "assigned_to_user_id": str(fremd.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["assigned_to"]["id"] == str(fremd.id)


@pytest.mark.django_db
def test_monteur_darf_eigene_aufgabe_abschliessen():
    creator = make_app_user("Dispo")
    client, monteur = _monteur_client()
    task = aufgabe_service.create_task(
        creator.id, title="Meine", assigned_to_user_id=monteur.id
    )
    r = client.post(f"/api/workflow/tasks/{task.id}/complete")
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "ERLEDIGT"


@pytest.mark.django_db
def test_monteur_bekommt_404_auf_fremder_aufgabe():
    creator = make_app_user("Dispo")
    client, _monteur = _monteur_client()
    fremd = make_app_user("Fremder Monteur")
    task = aufgabe_service.create_task(
        creator.id, title="Fremde", assigned_to_user_id=fremd.id
    )
    # Fremde Aufgabe existiert, darf aber weder abgeschlossen …
    r = client.post(f"/api/workflow/tasks/{task.id}/complete")
    assert r.status_code == 404, r.content
    # … noch überhaupt als existent erkennbar sein (404, nicht 403).
    assert "nicht gefunden" in r.json()["detail"].lower()


@pytest.mark.django_db
def test_monteur_403_auf_projekte_fail_closed():
    """Projekte-Liste wertet den Scope nicht aus → fail-closed 403 für EIGENE."""
    client, _monteur = _monteur_client()
    r = client.get("/api/workflow/projects")
    assert r.status_code == 403, r.content
    assert "eigene" in r.json()["detail"].lower()


# --- Einsätze (workflow.service_job) ---------------------------------------

def _service_job(actor_id, *, assignee_id=None):
    """Einen freigegebenen Auftrag mit einem Einsatz anlegen; optional zuweisen.

    Gibt die ServiceJob-Instanz zurück."""
    obj = property_service.create_property(
        actor_id, name="Einsatzhaus", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    principal = identity_service.create_person(
        actor_id, first_name="Petra", last_name="Prinzipal"
    )
    order = auftrag_service.create_work_order(
        actor_id, property_id=obj.id, title="Sockelrisse setzen"
    )
    auftrag_service.set_order_evidence(
        actor_id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        actor_id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        actor_id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(
            actor_id, work_order_id=order.id, to_status=to_status
        )
    job = einsatz_service.create_service_job(
        actor_id, work_order_id=order.id, scheduled_start=T0, scheduled_end=T1,
    )
    einsatz_service.advance_status(actor_id, service_job_id=job.id, to_status="GEPLANT")
    if assignee_id is not None:
        einsatz_service.assign_user(
            actor_id, service_job_id=job.id, assignee_user_id=assignee_id,
            role="TECHNICIAN",
        )
    return job


@pytest.mark.django_db
def test_monteur_sieht_nur_eigene_einsaetze():
    creator = make_app_user("Dispo")
    client, monteur = _monteur_client()
    eigen = _service_job(creator.id, assignee_id=monteur.id)
    _fremd = _service_job(creator.id)  # ohne Zuweisung an den Monteur

    r = client.get("/api/planung/einsaetze")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(eigen.id)


@pytest.mark.django_db
def test_monteur_sieht_eigenen_einsatz_detail():
    creator = make_app_user("Dispo")
    client, monteur = _monteur_client()
    eigen = _service_job(creator.id, assignee_id=monteur.id)
    r = client.get(f"/api/planung/einsaetze/{eigen.id}")
    assert r.status_code == 200, r.content
    assert r.json()["id"] == str(eigen.id)


@pytest.mark.django_db
def test_monteur_bekommt_404_auf_fremdem_einsatz_detail():
    creator = make_app_user("Dispo")
    client, _monteur = _monteur_client()
    fremd_job = _service_job(creator.id)  # nicht dem Monteur zugewiesen
    r = client.get(f"/api/planung/einsaetze/{fremd_job.id}")
    assert r.status_code == 404, r.content
    assert "nicht gefunden" in r.json()["detail"].lower()


@pytest.mark.django_db
def test_admin_sieht_alle_einsaetze():
    creator = make_app_user("Dispo")
    client, _admin = _admin_client()
    _service_job(creator.id)
    _service_job(creator.id)
    r = client.get("/api/planung/einsaetze")
    assert r.status_code == 200
    assert r.json()["total"] == 2
