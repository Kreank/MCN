"""API-Tests für Default-Dauer und Serientermine (Migration 0077)."""
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service

MO = datetime(2026, 7, 6, 8, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def termin(app_user):
    obj = property_service.create_property(
        app_user.id, name="Serienobjekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Wartungsrunde"
    )
    return planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=MO, scheduled_end=MO + timedelta(minutes=90),
    )


@pytest.mark.django_db
def test_kategorie_mit_dauer_anlegen_und_aendern(admin_client):
    r = admin_client.post(
        "/api/planung/kategorien",
        {"name": "Wartung Gastherme", "default_duration_minutes": 90},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    kat = r.json()
    assert kat["default_duration_minutes"] == 90

    r = admin_client.patch(
        f"/api/planung/kategorien/{kat['id']}",
        {"default_duration_minutes": 120},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["default_duration_minutes"] == 120


@pytest.mark.django_db
def test_dauer_bleibt_ohne_das_feld_unangetastet(admin_client):
    """Sentinel: Ein Update ohne `default_duration_minutes` darf sie nicht löschen."""
    kat = admin_client.post(
        "/api/planung/kategorien",
        {"name": "Begehung", "default_duration_minutes": 45},
        content_type="application/json",
    ).json()

    r = admin_client.patch(
        f"/api/planung/kategorien/{kat['id']}",
        {"name": "Begehung neu"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["default_duration_minutes"] == 45

    # Ausdrückliches null löscht sie dagegen.
    r = admin_client.patch(
        f"/api/planung/kategorien/{kat['id']}",
        {"default_duration_minutes": None},
        content_type="application/json",
    )
    assert r.json()["default_duration_minutes"] is None


@pytest.mark.django_db
def test_unsinnige_dauer_ist_422(admin_client):
    r = admin_client.post(
        "/api/planung/kategorien",
        {"name": "Kaputt", "default_duration_minutes": 0},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Dauer" in r.json()["detail"]


@pytest.mark.django_db
def test_serie_anlegen_und_lesen(admin_client, termin):
    r = admin_client.post(
        f"/api/planung/termine/{termin.id}/serie",
        {"intervall": "WOECHENTLICH", "anzahl": 3, "werktags": False},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["anzahl"] == 3
    assert len(body["erzeugt"]) == 3
    assert all(j["series_id"] == body["series_id"] for j in body["erzeugt"])
    assert all(j["status"] == "GEPLANT" for j in body["erzeugt"])

    # Die Serie enthält den Ausgangstermin als erstes Vorkommen.
    r = admin_client.get(f"/api/planung/termine/{termin.id}/serie")
    assert r.status_code == 200
    serie = r.json()
    assert len(serie) == 4
    assert serie[0]["id"] == str(termin.id)


@pytest.mark.django_db
def test_einzeltermin_hat_keine_serie(admin_client, termin):
    r = admin_client.get(f"/api/planung/termine/{termin.id}/serie")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.django_db
def test_serie_auf_unbekanntem_termin_ist_404(admin_client):
    r = admin_client.post(
        "/api/planung/termine/00000000-0000-4000-8000-000000000000/serie",
        {"intervall": "WOECHENTLICH", "anzahl": 2},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_zu_viele_wiederholungen_sind_422(admin_client, termin):
    r = admin_client.post(
        f"/api/planung/termine/{termin.id}/serie",
        {"intervall": "WOECHENTLICH", "anzahl": 99},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Anzahl" in r.json()["detail"]


@pytest.mark.django_db
def test_monteur_darf_keine_serie_anlegen(client_with_role, termin):
    """Serien sind Dispositionssache — Monteur-Scope EIGENE ist fail-closed."""
    c = client_with_role("MONTEUR")
    r = c.post(
        f"/api/planung/termine/{termin.id}/serie",
        {"intervall": "WOECHENTLICH", "anzahl": 2},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_serie_bleibt_lesbar_wenn_ein_vorkommen_abgesagt_wird(admin_client, termin):
    """Review-Fund: Ein in den Rückstand zurückgelegtes Vorkommen trägt
    `scheduled_start: null` — die Serienansicht lief darauf in einen 500er."""
    r = admin_client.post(
        f"/api/planung/termine/{termin.id}/serie",
        {"intervall": "WOECHENTLICH", "anzahl": 2, "werktags": False},
        content_type="application/json",
    )
    assert r.status_code == 201
    zweiter = r.json()["erzeugt"][0]["id"]

    r = admin_client.patch(
        f"/api/planung/termine/{zweiter}",
        {"scheduled_start": None, "reason": "Kunde hat abgesagt"},
        content_type="application/json",
    )
    assert r.status_code == 200

    r = admin_client.get(f"/api/planung/termine/{termin.id}/serie")
    assert r.status_code == 200, r.content
    reihe = r.json()
    assert len(reihe) == 3
    ohne_start = [j for j in reihe if j["scheduled_start"] is None]
    assert len(ohne_start) == 1
    assert ohne_start[0]["status"] == "UNGEPLANT"


@pytest.mark.django_db
def test_monteur_darf_die_serie_nicht_lesen(client_with_role, termin):
    c = client_with_role("MONTEUR")
    r = c.get(f"/api/planung/termine/{termin.id}/serie")
    assert r.status_code == 403
