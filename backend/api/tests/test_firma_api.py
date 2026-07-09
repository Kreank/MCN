"""API-Tests der Firmeneinstellungen (company) und der Mahnstufen-Pflege.

Prüft die Rechte-Tore (LESEN für alle, Ändern nur ADMINISTRATION/GF bzw.
invoicing-AENDERN) und die Mahnstufen-Lücken-Entscheidung als 422.
"""
import pytest

from db_core.models import CompanyProfile, DunningLevel


# --- Firmenprofil ----------------------------------------------------------

@pytest.mark.django_db
def test_profil_lesen_leer(admin_client):
    r = admin_client.get("/api/company/profile")
    assert r.status_code == 200
    assert r.json()["exists"] is False


@pytest.mark.django_db
def test_profil_anlegen_und_lesen(admin_client):
    r = admin_client.put(
        "/api/company/profile",
        data={"company_name": "Mitra Sanitär GmbH", "city": "Musterstadt"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["exists"] is True
    assert body["company_name"] == "Mitra Sanitär GmbH"
    # Persistiert als Singleton.
    assert CompanyProfile.objects.count() == 1
    g = admin_client.get("/api/company/profile").json()
    assert g["city"] == "Musterstadt"


@pytest.mark.django_db
def test_profil_lesen_fuer_nur_lesen_erlaubt(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = c.get("/api/company/profile")
    assert r.status_code == 200


@pytest.mark.django_db
def test_profil_aendern_nur_lesen_403(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = c.put(
        "/api/company/profile",
        data={"company_name": "Hack GmbH"},
        content_type="application/json",
    )
    assert r.status_code == 403


# --- Gewerke / Niederlassungen ---------------------------------------------

@pytest.mark.django_db
def test_gewerke_liste(admin_client):
    r = admin_client.get("/api/company/trades")
    assert r.status_code == 200
    codes = [t["code"] for t in r.json()]
    assert "SHK" in codes


@pytest.mark.django_db
def test_niederlassung_anlegen_und_deaktivieren(admin_client):
    r = admin_client.post(
        "/api/company/branches",
        data={"name": "Nord", "city": "Hamburg"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    bid = r.json()["id"]
    r2 = admin_client.put(
        f"/api/company/branches/{bid}",
        data={"active": False},
        content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2.json()["active"] is False


@pytest.mark.django_db
def test_niederlassung_anlegen_nur_lesen_403(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/company/branches",
        data={"name": "X"},
        content_type="application/json",
    )
    assert r.status_code == 403


# --- Mahnstufen -------------------------------------------------------------

@pytest.mark.django_db
def test_mahnstufen_liste_sechs(admin_client):
    r = admin_client.get("/api/buchhaltung/dunning-levels")
    assert r.status_code == 200
    levels = r.json()
    assert [lv["level"] for lv in levels] == [1, 2, 3, 4, 5, 6]
    assert all(lv["active"] for lv in levels)


@pytest.mark.django_db
def test_mahnstufe_pflegen(admin_client):
    r = admin_client.put(
        "/api/buchhaltung/dunning-levels/2",
        data={"label": "Erinnerung neu", "days_after_due": 12},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["label"] == "Erinnerung neu"
    assert DunningLevel.objects.get(level=2).days_after_due == 12


@pytest.mark.django_db
def test_mahnstufe_pflegen_nur_lesen_403(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = c.put(
        "/api/buchhaltung/dunning-levels/2",
        data={"label": "X"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_mahnstufe_mittlere_deaktivieren_422(admin_client):
    r = admin_client.put(
        "/api/buchhaltung/dunning-levels/3",
        data={"active": False},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "lückenlos" in r.json()["detail"]


@pytest.mark.django_db
def test_mahnstufe_hoechste_deaktivieren_ok(admin_client):
    r = admin_client.put(
        "/api/buchhaltung/dunning-levels/6",
        data={"active": False},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["active"] is False
