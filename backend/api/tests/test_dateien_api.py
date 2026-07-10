"""API-Tests der Datei-Ablage (/api/content).

Der Objektspeicher wird durch einen In-Memory-Fake ersetzt; MinIO muss nicht
laufen.

Geprüft wird das Rechte-Gating (ANLEGEN/LESEN/AENDERN im Modul `content`), der
Upload per multipart, die Ein-Ziel-Regel, die Typ-Whitelist und der Download —
insbesondere, dass er als `attachment` mit `nosniff` ausgeliefert wird und nicht
als Direkt-URL des Objektspeichers.
"""
import uuid
from hashlib import sha256

import pytest

from db_core import storage as storage_module
from db_core.services import property as property_service
from db_core.services import projekt as projekt_service

from .conftest import logged_in_client

BASE = "/api/content"
PDF = b"%PDF-1.4 Inhalt"


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
def projekt(app_user):
    obj = property_service.create_property(
        app_user.id, name="API-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    return projekt_service.create_project(
        app_user.id, name="API-Projekt", property_ids=[obj.id]
    )


def _upload(client, projekt, name="Angebot.pdf", inhalt=PDF, kategorie="DOKUMENT"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        f"{BASE}/files",
        data={
            "datei": SimpleUploadedFile(name, inhalt),
            "project_id": str(projekt.id),
            "link_category": kategorie,
        },
    )


@pytest.mark.django_db
def test_upload_und_liste(admin_client, projekt, fake_storage):
    r = _upload(admin_client, projekt)
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["original_filename"] == "Angebot.pdf"
    assert body["mime_type"] == "application/pdf"
    assert body["size_bytes"] == len(PDF)
    assert body["link_category"] == "DOKUMENT"

    liste = admin_client.get(f"{BASE}/files?project_id={projekt.id}").json()
    assert liste["total"] == 1
    assert liste["items"][0]["file_id"] == body["file_id"]


@pytest.mark.django_db
def test_download_ist_attachment_mit_nosniff(admin_client, projekt, fake_storage):
    """Kein Inline-Rendering, kein MIME-Sniffing — hochgeladener Inhalt wird nie
    im Ursprung der Anwendung ausgeführt."""
    file_id = _upload(admin_client, projekt).json()["file_id"]
    r = admin_client.get(f"{BASE}/files/{file_id}/download")
    assert r.status_code == 200
    assert r.content == PDF
    assert r["Content-Type"] == "application/pdf"
    assert r["Content-Disposition"].startswith("attachment;")
    assert r["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db
def test_download_dateiname_ausserhalb_latin1(admin_client, projekt, fake_storage):
    """HTTP-Kopfzeilen sind latin-1. Ein Name mit Emoji oder Euro-Zeichen ließ die
    Antwort früher werfen (500) — hochladen ging, herunterladen nicht."""
    file_id = _upload(admin_client, projekt, name="Angebot €100 ✓.pdf").json()["file_id"]
    r = admin_client.get(f"{BASE}/files/{file_id}/download")
    assert r.status_code == 200
    assert r.content == PDF
    disposition = r["Content-Disposition"]
    assert disposition.startswith("attachment;")
    # RFC 5987: prozentkodiert, damit der Browser den Namen wiederherstellt.
    assert "filename*=utf-8''" in disposition
    assert "%E2%82%AC" in disposition  # €


@pytest.mark.django_db
def test_download_dateiname_mit_anfuehrungszeichen(admin_client, projekt, fake_storage):
    """Ein Anführungszeichen darf nicht aus dem Quoting ausbrechen."""
    file_id = _upload(admin_client, projekt, name='Anlage "A".pdf').json()["file_id"]
    r = admin_client.get(f"{BASE}/files/{file_id}/download")
    assert r.status_code == 200
    assert r["Content-Disposition"] == 'attachment; filename="Anlage \\"A\\".pdf"'


@pytest.mark.django_db
def test_download_unbekannt_404(admin_client, fake_storage):
    r = admin_client.get(f"{BASE}/files/{uuid.uuid4()}/download")
    assert r.status_code == 404


@pytest.mark.django_db
def test_upload_unzulaessiger_typ_422(admin_client, projekt, fake_storage):
    r = _upload(admin_client, projekt, name="schad.html", inhalt=b"<script>")
    assert r.status_code == 422, r.content
    assert "nicht zugelassen" in r.json()["detail"]


@pytest.mark.django_db
def test_upload_ohne_ziel_422(admin_client, fake_storage):
    from django.core.files.uploadedfile import SimpleUploadedFile

    r = admin_client.post(
        f"{BASE}/files", data={"datei": SimpleUploadedFile("a.pdf", PDF)}
    )
    assert r.status_code == 422
    assert "genau einem Objekt" in r.json()["detail"]


@pytest.mark.django_db
def test_upload_zwei_ziele_422(admin_client, projekt, app_user, fake_storage):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from db_core.services import identity as identity_service

    person = identity_service.create_person(app_user.id, first_name="A", last_name="B")
    r = admin_client.post(
        f"{BASE}/files",
        data={
            "datei": SimpleUploadedFile("a.pdf", PDF),
            "project_id": str(projekt.id),
            "party_id": str(person.id),
        },
    )
    assert r.status_code == 422
    assert "genau einem Objekt" in r.json()["detail"]


@pytest.mark.django_db
def test_verknuepfung_loesen(admin_client, projekt, fake_storage):
    link_id = _upload(admin_client, projekt).json()["link_id"]
    r = admin_client.delete(f"{BASE}/links/{link_id}")
    assert r.status_code == 204
    assert admin_client.get(f"{BASE}/files?project_id={projekt.id}").json()["total"] == 0


# --- Rechte -----------------------------------------------------------------

@pytest.mark.django_db
def test_upload_ohne_login_401(anonymous_client, projekt, fake_storage):
    r = _upload(anonymous_client, projekt)
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_nur_lesen_darf_nicht_hochladen(projekt, fake_storage):
    """NUR_LESEN hat content/LESEN, aber kein content/ANLEGEN."""
    leser = logged_in_client("NUR_LESEN")
    assert _upload(leser, projekt).status_code == 403


@pytest.mark.django_db
def test_nur_lesen_darf_lesen(admin_client, projekt, fake_storage):
    _upload(admin_client, projekt)
    leser = logged_in_client("NUR_LESEN")
    r = leser.get(f"{BASE}/files?project_id={projekt.id}")
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.django_db
def test_monteur_darf_hochladen(projekt, fake_storage):
    """MONTEUR hat content/ANLEGEN (Fotos von der Baustelle)."""
    monteur = logged_in_client("MONTEUR")
    r = _upload(monteur, projekt, name="vorher.jpg", inhalt=b"\xff\xd8\xff",
                kategorie="FOTO_VORHER")
    assert r.status_code == 201, r.content
    assert r.json()["link_category"] == "FOTO_VORHER"
