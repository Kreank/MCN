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
def test_datev_abschlagsmodus_default_und_umstellen(admin_client):
    """Der Abschlags-Buchungsmodus (0063) ist am Firmenprofil pflegbar; Default
    bleibt ERLOES (Bestandsverhalten), ein unbekannter Wert ist 422."""
    r = admin_client.put(
        "/api/company/profile",
        data={"company_name": "Mitra Sanitär GmbH"},
        content_type="application/json",
    )
    assert r.json()["datev_advance_mode"] == "ERLOES"

    r = admin_client.put(
        "/api/company/profile",
        data={"datev_advance_mode": "ANZAHLUNG",
              "datev_advance_account_full": "1718"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["datev_advance_mode"] == "ANZAHLUNG"
    assert r.json()["datev_advance_account_full"] == "1718"

    r = admin_client.put(
        "/api/company/profile",
        data={"datev_advance_mode": "IRGENDWAS"},
        content_type="application/json",
    )
    assert r.status_code == 422
    # Ein geleertes Feld bedeutet „unverändert" (die Spalte ist NOT NULL) — kein 500.
    r = admin_client.put(
        "/api/company/profile",
        data={"datev_advance_mode": ""},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["datev_advance_mode"] == "ANZAHLUNG"


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


# --- Onboarding / Erste Schritte -------------------------------------------

@pytest.mark.django_db
def test_onboarding_frisch_alles_offen(admin_client):
    """Frische DB: kein Meilenstein erreicht → alle Flags False."""
    r = admin_client.get("/api/company/onboarding")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "firmenprofil", "logo", "bankdaten", "mailkonto",
        "kontakt", "liegenschaft", "projekt", "beleg",
    }
    assert all(v is False for v in body.values())


@pytest.mark.django_db
def test_onboarding_flags_nach_setup(admin_client, app_user):
    """Firmenprofil (mit IBAN) + erster Kontakt setzen die passenden Flags."""
    from db_core.services import identity as identity_service
    admin_client.put(
        "/api/company/profile",
        data={"company_name": "Mitra GmbH", "iban": "DE02701500000000594937"},
        content_type="application/json",
    )
    identity_service.create_person(app_user.id, "Erika", "Muster")

    body = admin_client.get("/api/company/onboarding").json()
    assert body["firmenprofil"] is True
    assert body["bankdaten"] is True
    assert body["kontakt"] is True
    # Nicht getan → weiterhin offen.
    assert body["logo"] is False
    assert body["liegenschaft"] is False
    assert body["mailkonto"] is False


@pytest.mark.django_db
def test_onboarding_lesen_fuer_nur_lesen(client_with_role):
    """company/LESEN hat jede Rolle — die Checkliste ist für alle sichtbar."""
    c = client_with_role("NUR_LESEN")
    assert c.get("/api/company/onboarding").status_code == 200


@pytest.mark.django_db
def test_onboarding_anonym_401(anonymous_client):
    assert anonymous_client.get("/api/company/onboarding").status_code == 401


# --- Akquisekanäle / Quellen -----------------------------------------------

@pytest.mark.django_db
def test_quellen_liste_seed(admin_client):
    """Der Katalog hat Start-Kanäle (0049)."""
    r = admin_client.get("/api/company/acquisition-sources")
    assert r.status_code == 200
    codes = [s["code"] for s in r.json()]
    assert "EMPFEHLUNG" in codes and "WEBSITE" in codes


@pytest.mark.django_db
def test_quelle_anlegen_und_deaktivieren(admin_client):
    r = admin_client.post(
        "/api/company/acquisition-sources",
        data={"code": "PARTNER", "label": "Partnerbetrieb", "sort_order": 15},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    sid = r.json()["id"]
    assert r.json()["code"] == "PARTNER"
    r2 = admin_client.put(
        f"/api/company/acquisition-sources/{sid}",
        data={"active": False, "label": "Partner (alt)"},
        content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2.json()["active"] is False
    assert r2.json()["label"] == "Partner (alt)"
    # nur aktive
    aktiv = admin_client.get(
        "/api/company/acquisition-sources?include_inactive=false"
    ).json()
    assert all(s["id"] != sid for s in aktiv)


@pytest.mark.django_db
def test_quelle_doppelter_code_422(admin_client):
    r = admin_client.post(
        "/api/company/acquisition-sources",
        data={"code": "EMPFEHLUNG", "label": "Doppelt"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_quelle_ungueltiger_code_422(admin_client):
    r = admin_client.post(
        "/api/company/acquisition-sources",
        data={"code": "kein code!", "label": "X"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_quelle_anlegen_nur_lesen_403(client_with_role):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/company/acquisition-sources",
        data={"code": "X2", "label": "X"},
        content_type="application/json",
    )
    assert r.status_code == 403
