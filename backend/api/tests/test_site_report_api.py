"""API-Tests der Baustellenbericht-Endpoints (workflow.site_report).

Deckt ab: Anlegen/Lesen/Ändern im ENTWURF, Besiegeln durch Kundenunterschrift
(ENTWURF → UNTERZEICHNET) mitsamt der Unveränderlichkeit danach, Foto-Anhang über
die Datei-API mit `site_report_id`, sowie die Rechte-/Auth-Tore.
"""
import base64
import uuid
from hashlib import sha256

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from db_core import storage as storage_module
from db_core.models import SiteReport
from db_core.services import auftrag as auftrag_service
from db_core.services import property as property_service

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


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Baustelle", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Bad sanieren"
    )
    return {"app_user": app_user, "obj": obj, "order": order}


# --- Lesen -----------------------------------------------------------------

@pytest.mark.django_db
def test_liste_leer(admin_client, seeded):
    r = admin_client.get(f"/api/workflow/site_reports?work_order_id={seeded['order'].id}")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


@pytest.mark.django_db
def test_liste_unbekannter_auftrag_404(admin_client, db):
    r = admin_client.get(f"/api/workflow/site_reports?work_order_id={uuid.uuid4()}")
    assert r.status_code == 404


# --- Anlegen ---------------------------------------------------------------

@pytest.mark.django_db
def test_anlegen_und_lesen(client, seeded):
    c = logged_in_client("ADMINISTRATION")
    r = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "Fliesen entfernt, Estrich vorbereitet.",
            "hours_worked": "6.5",
            "weather": "sonnig",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "ENTWURF"
    assert body["activity_text"].startswith("Fliesen")
    assert body["hours_worked"] == "6.50"
    assert SiteReport.objects.filter(id=body["id"]).exists()

    liste = c.get(
        f"/api/workflow/site_reports?work_order_id={seeded['order'].id}"
    ).json()
    assert liste["total"] == 1


@pytest.mark.django_db
def test_anlegen_leere_taetigkeit_422(client, seeded):
    c = logged_in_client("ADMINISTRATION")
    r = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "   ",
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_anlegen_stunden_ueberlauf_422_nicht_500(client, seeded):
    # numeric(6,2): 9999.999 rundet in der DB auf 10000.00 (22003). Der Service
    # muss das VOR dem Insert als 422 abfangen, nicht als 500 durchreichen.
    c = logged_in_client("ADMINISTRATION")
    r = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "x",
            "hours_worked": "9999.999",
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_anlegen_ohne_login_abgelehnt(anonymous_client, seeded):
    r = anonymous_client.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "Anon",
        },
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


# --- Ändern ----------------------------------------------------------------

@pytest.mark.django_db
def test_aendern_im_entwurf(client, seeded):
    c = logged_in_client("ADMINISTRATION")
    rid = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "erste Fassung",
        },
        content_type="application/json",
    ).json()["id"]
    r = c.put(
        f"/api/workflow/site_reports/{rid}",
        data={"remarks": "Nachtrag", "activity_text": "korrigierte Fassung"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["remarks"] == "Nachtrag"
    assert r.json()["activity_text"] == "korrigierte Fassung"


# --- Unterschreiben (besiegeln) --------------------------------------------

def _sign_payload(name="Klara Kundin", png=PNG_1x1):
    return {
        "signed_by_name": name,
        "signature_png_base64": base64.b64encode(png).decode("ascii"),
    }


@pytest.mark.django_db
def test_unterschreiben_besiegelt_und_friert_ein(client, seeded, fake_storage):
    c = logged_in_client("ADMINISTRATION")
    rid = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "Arbeiten abgeschlossen.",
        },
        content_type="application/json",
    ).json()["id"]

    r = c.post(
        f"/api/workflow/site_reports/{rid}/sign",
        data=_sign_payload(),
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "UNTERZEICHNET"
    assert body["signed_by_name"] == "Klara Kundin"
    assert body["signed_at"] is not None
    assert body["signature_file_id"] is not None

    # Unterzeichnet ⇒ eingefroren: erneutes Ändern wird abgelehnt.
    r2 = c.put(
        f"/api/workflow/site_reports/{rid}",
        data={"remarks": "nachträglich"},
        content_type="application/json",
    )
    assert r2.status_code == 422
    # Erneutes Signieren ebenfalls.
    r3 = c.post(
        f"/api/workflow/site_reports/{rid}/sign",
        data=_sign_payload(),
        content_type="application/json",
    )
    assert r3.status_code == 422


@pytest.mark.django_db
def test_unterschreiben_ohne_name_422(client, seeded, fake_storage):
    c = logged_in_client("ADMINISTRATION")
    rid = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "x",
        },
        content_type="application/json",
    ).json()["id"]
    r = c.post(
        f"/api/workflow/site_reports/{rid}/sign",
        data=_sign_payload(name="   "),
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_unterschreiben_kein_png_422(client, seeded, fake_storage):
    c = logged_in_client("ADMINISTRATION")
    rid = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "x",
        },
        content_type="application/json",
    ).json()["id"]
    r = c.post(
        f"/api/workflow/site_reports/{rid}/sign",
        data={
            "signed_by_name": "Klara",
            "signature_png_base64": base64.b64encode(b"nicht-png").decode("ascii"),
        },
        content_type="application/json",
    )
    assert r.status_code == 422


# --- Fotos über die Datei-API ----------------------------------------------

@pytest.mark.django_db
def test_foto_anhang_ueber_dateien_api(client, seeded, fake_storage):
    c = logged_in_client("ADMINISTRATION")
    rid = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "x",
        },
        content_type="application/json",
    ).json()["id"]

    r = c.post(
        "/api/content/files",
        data={
            "datei": SimpleUploadedFile("vorher.png", PNG_1x1, content_type="image/png"),
            "site_report_id": rid,
            "link_category": "FOTO_VORHER",
        },
    )
    assert r.status_code == 201, r.content
    assert r.json()["link_category"] == "FOTO_VORHER"

    liste = c.get(f"/api/content/files?site_report_id={rid}").json()
    assert liste["total"] == 1
    assert liste["items"][0]["original_filename"] == "vorher.png"


# --- Bericht-PDF ------------------------------------------------------------

@pytest.mark.django_db
def test_bericht_pdf_entwurf(client, seeded, fake_storage):
    """Das Bericht-PDF rendert im ENTWURF (mit Aufdruck) — on-the-fly, 200."""
    c = logged_in_client("ADMINISTRATION")
    r = c.post(
        "/api/workflow/site_reports",
        data={
            "work_order_id": str(seeded["order"].id),
            "report_date": "2026-07-11",
            "activity_text": "Fliesen entfernt, Estrich vorbereitet.",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    report_id = r.json()["id"]
    pdf = c.get(f"/api/workflow/site_reports/{report_id}/pdf")
    assert pdf.status_code == 200
    assert pdf["Content-Type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"


@pytest.mark.django_db
def test_bericht_pdf_unbekannt_404(client, db):
    c = logged_in_client("ADMINISTRATION")
    r = c.get(f"/api/workflow/site_reports/{uuid.uuid4()}/pdf")
    assert r.status_code == 404
