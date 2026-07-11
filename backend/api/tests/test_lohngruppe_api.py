"""API-Tests der Lohn-/Maschinengruppen (pricing.wage_group).

Prüft die Rechte-Tore (LESEN zum Anzeigen, ANLEGEN/AENDERN zum Pflegen), die
Fachvalidierung als 422 (spiegelt die DB-CHECKs: Name eindeutig/nicht leer, Art
LOHN|MASCHINE, Sätze >= 0) und das Deaktivieren statt Löschen.
"""
import pytest

from db_core.models import WageGroup

from .conftest import logged_in_client


def _create(client, **overrides):
    payload = {"name": "Monteur", "kind": "LOHN", "hourly_rate": "65.00"}
    payload.update(overrides)
    return client.post(
        "/api/pricing/wage-groups", data=payload, content_type="application/json"
    )


@pytest.mark.django_db
def test_anlegen_und_lesen(admin_client):
    r = _create(admin_client, name="Meister", hourly_rate="82.50", cost_rate="48.00")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["name"] == "Meister"
    assert body["kind"] == "LOHN"
    assert body["hourly_rate"] == "82.50"
    assert body["cost_rate"] == "48.00"
    assert body["status"] == "AKTIV"

    liste = admin_client.get("/api/pricing/wage-groups").json()
    assert any(g["name"] == "Meister" for g in liste)


@pytest.mark.django_db
def test_kostensatz_optional(admin_client):
    r = _create(admin_client, name="Hilfskraft", cost_rate=None)
    assert r.status_code == 201, r.content
    assert r.json()["cost_rate"] is None


@pytest.mark.django_db
def test_maschinengruppe(admin_client):
    r = _create(admin_client, name="Bagger", kind="MASCHINE", hourly_rate="120.00")
    assert r.status_code == 201, r.content
    assert r.json()["kind"] == "MASCHINE"


@pytest.mark.django_db
def test_doppelter_name_422(admin_client):
    assert _create(admin_client, name="Monteur").status_code == 201
    r = _create(admin_client, name="Monteur")
    assert r.status_code == 422
    assert "existiert bereits" in r.json()["detail"]


@pytest.mark.django_db
def test_leerer_name_422(admin_client):
    r = _create(admin_client, name="   ")
    assert r.status_code == 422


@pytest.mark.django_db
def test_ungueltige_art_422(admin_client):
    r = _create(admin_client, kind="ROBOTER")
    assert r.status_code == 422
    assert "LOHN oder MASCHINE" in r.json()["detail"]


@pytest.mark.django_db
def test_negativer_satz_422(admin_client):
    r = _create(admin_client, hourly_rate="-5.00")
    assert r.status_code == 422


@pytest.mark.django_db
def test_zu_grosser_satz_422(admin_client):
    # numeric(12,2) fasst max 9.999.999.999,99 — darüber wäre es ein DB-Overflow
    # (DataError → 500); der Service muss das als 422 abfangen.
    r = _create(admin_client, hourly_rate="10000000000")
    assert r.status_code == 422


@pytest.mark.django_db
def test_nan_satz_422(admin_client):
    # NaN würde den DB-CHECK >= 0 unterlaufen (NaN >= 0 ist in Postgres TRUE);
    # der Service muss es als ungültige Zahl abweisen.
    r = _create(admin_client, hourly_rate="NaN")
    assert r.status_code == 422


@pytest.mark.django_db
def test_umbenennen(admin_client):
    gid = _create(admin_client, name="Alt").json()["id"]
    r = admin_client.put(
        f"/api/pricing/wage-groups/{gid}",
        data={"name": "Neu", "hourly_rate": "70.00"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["name"] == "Neu"
    assert r.json()["hourly_rate"] == "70.00"


@pytest.mark.django_db
def test_umbenennen_auf_bestehenden_namen_422(admin_client):
    _create(admin_client, name="A")
    gid = _create(admin_client, name="B").json()["id"]
    r = admin_client.put(
        f"/api/pricing/wage-groups/{gid}",
        data={"name": "A"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_deaktivieren_statt_loeschen(admin_client):
    gid = _create(admin_client, name="Auslauf").json()["id"]
    r = admin_client.put(
        f"/api/pricing/wage-groups/{gid}",
        data={"status": "INAKTIV"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["status"] == "INAKTIV"
    # Datensatz bleibt bestehen (kein Löschen), nur der Status kippt.
    assert WageGroup.objects.filter(id=gid, status="INAKTIV").exists()

    aktiv = admin_client.get(
        "/api/pricing/wage-groups?include_inactive=false"
    ).json()
    assert all(g["id"] != gid for g in aktiv)
    alle = admin_client.get("/api/pricing/wage-groups").json()
    assert any(g["id"] == gid for g in alle)


@pytest.mark.django_db
def test_ungueltiger_status_422(admin_client):
    gid = _create(admin_client, name="X").json()["id"]
    r = admin_client.put(
        f"/api/pricing/wage-groups/{gid}",
        data={"status": "GELOESCHT"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_anlegen_ohne_recht_403(db):
    # TECHNISCHE_LEITUNG hat pricing/LESEN, aber kein ANLEGEN.
    client = logged_in_client("TECHNISCHE_LEITUNG")
    assert client.get("/api/pricing/wage-groups").status_code == 200
    assert _create(client).status_code == 403


@pytest.mark.django_db
def test_anonym_401(anonymous_client):
    assert anonymous_client.get("/api/pricing/wage-groups").status_code == 401
