"""API-Tests der Firmeneinstellungen (company) und der Mahnstufen-Pflege.

Prüft die Rechte-Tore (LESEN für alle, Ändern nur ADMINISTRATION/GF bzw.
invoicing-AENDERN) und die Mahnstufen-Lücken-Entscheidung als 422.
"""
from hashlib import sha256

import pytest

from db_core import storage as storage_module
from db_core.models import CompanyProfile, DunningLevel

from .conftest import logged_in_client

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        payload = bytes(data)
        self.objects[key] = payload
        return storage_module.ObjectInfo(
            storage_key=key, sha256=sha256(payload).hexdigest(), size_bytes=len(payload)
        )

    def get_object(self, key):
        if key not in self.objects:
            raise storage_module.StorageError(key)
        return self.objects[key]

    def remove_object(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    return fake


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


# --- Firmenlogo ------------------------------------------------------------

def _profil_anlegen(client, name="Logo GmbH"):
    r = client.put(
        "/api/company/profile",
        data={"company_name": name},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content


@pytest.mark.django_db
def test_logo_hochladen_setzt_has_logo(admin_client, fake_storage):
    from django.core.files.uploadedfile import SimpleUploadedFile

    _profil_anlegen(admin_client)
    r = admin_client.post(
        "/api/company/profile/logo",
        data={"datei": SimpleUploadedFile("logo.png", PNG_1x1)},
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["has_logo"] is True
    assert body["logo_file_id"] is not None
    # Auch das Profil-Detail meldet has_logo=True.
    assert admin_client.get("/api/company/profile").json()["has_logo"] is True


@pytest.mark.django_db
def test_logo_abruf_ist_inline_mit_nosniff(admin_client, fake_storage):
    from django.core.files.uploadedfile import SimpleUploadedFile

    _profil_anlegen(admin_client)
    admin_client.post(
        "/api/company/profile/logo",
        data={"datei": SimpleUploadedFile("logo.png", PNG_1x1)},
    )
    r = admin_client.get("/api/company/profile/logo")
    assert r.status_code == 200
    assert r.content == PNG_1x1
    assert r["Content-Type"] == "image/png"
    assert r["Content-Disposition"].startswith("inline;")
    assert r["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db
def test_logo_abruf_ohne_logo_404(admin_client, fake_storage):
    _profil_anlegen(admin_client)
    r = admin_client.get("/api/company/profile/logo")
    assert r.status_code == 404


@pytest.mark.django_db
def test_logo_ungueltiger_typ_422(admin_client, fake_storage):
    from django.core.files.uploadedfile import SimpleUploadedFile

    _profil_anlegen(admin_client)
    r = admin_client.post(
        "/api/company/profile/logo",
        data={"datei": SimpleUploadedFile("logo.svg", b"<svg>evil</svg>")},
    )
    assert r.status_code == 422
    assert "PNG- oder JPEG" in r.json()["detail"]


@pytest.mark.django_db
def test_logo_entfernen_setzt_has_logo_false(admin_client, fake_storage):
    from django.core.files.uploadedfile import SimpleUploadedFile

    _profil_anlegen(admin_client)
    admin_client.post(
        "/api/company/profile/logo",
        data={"datei": SimpleUploadedFile("logo.png", PNG_1x1)},
    )
    r = admin_client.delete("/api/company/profile/logo")
    assert r.status_code == 200
    assert r.json()["has_logo"] is False
    # Abruf danach → 404 (Referenz weg).
    assert admin_client.get("/api/company/profile/logo").status_code == 404


@pytest.mark.django_db
def test_logo_hochladen_nur_lesen_403(client_with_role, fake_storage, admin_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    _profil_anlegen(admin_client)
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/company/profile/logo",
        data={"datei": SimpleUploadedFile("logo.png", PNG_1x1)},
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_logo_abruf_fuer_nur_lesen_erlaubt(client_with_role, fake_storage, admin_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    _profil_anlegen(admin_client)
    admin_client.post(
        "/api/company/profile/logo",
        data={"datei": SimpleUploadedFile("logo.png", PNG_1x1)},
    )
    c = client_with_role("NUR_LESEN")
    r = c.get("/api/company/profile/logo")
    assert r.status_code == 200
    assert r.content == PNG_1x1


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
