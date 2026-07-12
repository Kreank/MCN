"""API-Tests der Planungs-Stammdaten: Terminkategorien, Ressourcen, deren
Zuordnung zum Einsatz und die Ressourcen-Bahnen der Plantafel.

Deckt ab: Anlegen/Listen (Admin), Rechte (403 ohne ANLEGEN; 403 für MONTEUR
mit Scope EIGENE auf den fail-closed Stammdaten-Endpunkten), Kategorie-Zuordnung
am Einsatz und Plantafel-Ressourcenbahnen.
"""
from datetime import datetime, timezone as dt_timezone

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service

T0 = datetime(2026, 7, 20, 8, 0, tzinfo=dt_timezone.utc)
T1 = datetime(2026, 7, 20, 12, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def order(app_user):
    obj = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    return auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag"
    )


# --- Kategorien -------------------------------------------------------------

@pytest.mark.django_db
def test_kategorie_anlegen_und_listen(admin_client):
    r = admin_client.post(
        "/api/planung/kategorien",
        data={"name": "Vor-Ort-Termin", "color_token": "SAGE"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["color_token"] == "SAGE"

    liste = admin_client.get("/api/planung/kategorien").json()
    assert any(c["name"] == "Vor-Ort-Termin" for c in liste)


@pytest.mark.django_db
def test_kategorie_ungueltige_farbe_422(admin_client):
    r = admin_client.post(
        "/api/planung/kategorien",
        data={"name": "X", "color_token": "KNALLROT"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_kategorie_archivieren(admin_client, app_user):
    c = planung_service.create_category(app_user.id, name="Alt")
    r = admin_client.post(f"/api/planung/kategorien/{c.id}/archivieren")
    assert r.status_code == 200
    assert r.json()["status"] == "ARCHIVIERT"
    # Archivierte erscheinen nur mit include_archived.
    assert not any(
        x["id"] == str(c.id) for x in admin_client.get("/api/planung/kategorien").json()
    )
    assert any(
        x["id"] == str(c.id)
        for x in admin_client.get(
            "/api/planung/kategorien?include_archived=true"
        ).json()
    )


@pytest.mark.django_db
def test_kategorie_am_einsatz_setzen(admin_client, app_user, order):
    c = planung_service.create_category(app_user.id, name="Büro", color_token="AMBER")
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    r = admin_client.post(
        f"/api/planung/einsaetze/{job.id}/kategorie",
        data={"category_id": str(c.id)},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["category"]["name"] == "Büro"
    assert r.json()["category"]["color_token"] == "AMBER"


# --- Ressourcen -------------------------------------------------------------

@pytest.mark.django_db
def test_ressource_anlegen_und_listen(admin_client):
    r = admin_client.post(
        "/api/planung/ressourcen",
        data={"name": "VW Crafter", "resource_type": "FAHRZEUG"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["resource_number"].startswith("RES-")
    liste = admin_client.get("/api/planung/ressourcen").json()
    assert any(x["name"] == "VW Crafter" for x in liste)


@pytest.mark.django_db
def test_ressource_ungueltiger_typ_422(admin_client):
    r = admin_client.post(
        "/api/planung/ressourcen",
        data={"name": "X", "resource_type": "RAKETE"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_ressource_am_einsatz_und_plantafel(admin_client, app_user, order):
    """Ressource einem verplanten Einsatz zuordnen → Plantafel liefert die
    Ressourcen-Bahn und der Einsatz die resource_ids."""
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=T0, scheduled_end=T1,
    )
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    r = admin_client.post(
        f"/api/planung/einsaetze/{job.id}/ressourcen",
        data={"resource_id": str(res.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["resource"]["name"] == "VW Crafter"
    assert r.json()["warnings"] == []

    board = admin_client.get(
        "/api/planung/plantafel?date_from=2026-07-20&date_to=2026-07-20"
    ).json()
    # Bahnen sind jetzt EINE Liste (Mitarbeiter + Betriebsmittel, `kind` trennt).
    lanes = [lane for lane in board["lanes"] if lane["kind"] == "RESOURCE"]
    assert any(lane["id"] == str(res.id) for lane in lanes)
    job_row = next(j for j in board["jobs"] if j["id"] == str(job.id))
    assert str(res.id) in job_row["resource_ids"]


@pytest.mark.django_db
def test_ressource_doppelbelegung_warnung(admin_client, app_user, order):
    job_a = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0, scheduled_end=T1
    )
    job_b = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id, scheduled_start=T0, scheduled_end=T1
    )
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    planung_service.assign_resource(
        app_user.id, service_job_id=job_a.id, resource_id=res.id
    )
    r = admin_client.post(
        f"/api/planung/einsaetze/{job_b.id}/ressourcen",
        data={"resource_id": str(res.id)},
        content_type="application/json",
    )
    assert r.status_code == 201
    assert len(r.json()["warnings"]) == 1


@pytest.mark.django_db
def test_ressource_entfernen(admin_client, app_user, order):
    job = einsatz_service.create_service_job(app_user.id, work_order_id=order.id)
    res = planung_service.create_resource(
        app_user.id, name="VW Crafter", resource_type="FAHRZEUG"
    )
    planung_service.assign_resource(
        app_user.id, service_job_id=job.id, resource_id=res.id
    )
    r = admin_client.delete(f"/api/planung/einsaetze/{job.id}/ressourcen/{res.id}")
    assert r.status_code == 200


# --- Rechte -----------------------------------------------------------------

@pytest.mark.django_db
def test_nur_lesen_darf_listen_aber_nicht_anlegen(client_with_role):
    c = client_with_role("NUR_LESEN")
    assert c.get("/api/planung/kategorien").status_code == 200
    assert c.get("/api/planung/ressourcen").status_code == 200
    r = c.post(
        "/api/planung/kategorien",
        data={"name": "X"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_monteur_fail_closed_auf_stammdaten(client_with_role):
    """MONTEUR hat workflow=EIGENE → die fail-closed Stammdaten-Endpunkte
    liefern 403 (require, nicht require_scoped)."""
    c = client_with_role("MONTEUR")
    assert c.get("/api/planung/kategorien").status_code == 403
    assert c.get("/api/planung/ressourcen").status_code == 403
