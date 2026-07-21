"""Pflegbare Dateikategorien (Befund A4/A5, Migration 0127).

Sascha: „Gerne auch die Möglichkeit einbauen, eigene Kategorien einfügen,
bearbeiten und löschen/deaktivieren zu können."

Vorher war `link_category` Freitext in der DB mit einer hartkodierten Liste im
Service. Wer eine eigene Kategorie wollte, musste den Code ändern — und nichts
hinderte einen anderen Schreibpfad daran, „foto_vorher" zu setzen.

Der Kern dieser Tests ist der **Schutz der Systemkategorien**: Vier Codes
stehen in partiellen UNIQUE-Indizes (0032/0042/0059), die gegen den
Literalwert prüfen. Ein umbenannter oder deaktivierter Code ließe den
zugehörigen Index still ins Leere laufen.
"""
from hashlib import sha256

import pytest

from db_core import storage as storage_module
from db_core.models import FileCategory
from db_core.services import dateien as dateien_service


class FakeStorage:
    """Objektspeicher im Arbeitsspeicher — dieselbe Form wie in
    `test_dateien_api.py`. Die Kategorienlogik hat mit MinIO nichts zu tun;
    ein laufender Container wäre hier nur eine Fremdabhängigkeit."""

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


@pytest.mark.django_db
def test_bestand_ist_uebernommen(admin_client):
    """Die Migration seedet die bisher hartkodierte Liste."""
    r = admin_client.get("/api/content/file-categories")
    assert r.status_code == 200, r.content
    codes = {k["code"] for k in r.json()}
    for erwartet in dateien_service.LINK_KATEGORIEN:
        assert erwartet in codes, f"{erwartet} fehlt in der Codeliste"


@pytest.mark.django_db
def test_systemkategorien_sind_aus_der_auswahl_raus(admin_client):
    """ARTIKELBILD, ATTEST, BELEG_PDF und E_RECHNUNG entstehen als Nebenwirkung
    anderer Vorgänge — sie gehören in kein Auswahlfeld."""
    r = admin_client.get("/api/content/file-categories?ohne_system=true")
    assert r.status_code == 200
    codes = {k["code"] for k in r.json()}
    assert "DOKUMENT" in codes
    for system in ("ARTIKELBILD", "ATTEST", "BELEG_PDF", "E_RECHNUNG"):
        assert system not in codes


@pytest.mark.django_db
def test_eigene_kategorie_anlegen(admin_client):
    r = admin_client.post(
        "/api/content/file-categories",
        data={"label": "Baustellenbericht", "sort_order": 45},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    # Der Code wird aus der Bezeichnung abgeleitet und normalisiert.
    assert body["code"] == "BAUSTELLENBERICHT"
    assert body["is_system"] is False
    assert body["status"] == "AKTIV"


@pytest.mark.django_db
def test_code_wird_normalisiert(admin_client):
    """`Foto Innen-Bereich` → `FOTO_INNEN_BEREICH`.

    Ein Code mit Leerzeichen oder wechselnder Schreibweise wäre der Anfang
    genau des Auseinanderlaufens, gegen das die Liste antritt.
    """
    r = admin_client.post(
        "/api/content/file-categories",
        data={"label": "Foto Innen-Bereich"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["code"] == "FOTO_INNEN_BEREICH"


@pytest.mark.django_db
def test_doppelter_code_wird_abgelehnt(admin_client):
    r = admin_client.post(
        "/api/content/file-categories",
        data={"label": "Dokument"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "existiert bereits" in r.json()["detail"]


@pytest.mark.django_db
def test_bezeichnung_aendern_laesst_den_code_stehen(admin_client):
    """Was der Nutzer sieht, ist das Label — der Code hängt in jeder Datei."""
    kategorie = FileCategory.objects.get(code="PLAN")
    r = admin_client.patch(
        f"/api/content/file-categories/{kategorie.id}",
        data={"label": "Bauplan"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["label"] == "Bauplan"
    assert r.json()["code"] == "PLAN"


@pytest.mark.django_db
def test_deaktivieren_und_wieder_aktivieren(admin_client):
    kategorie = FileCategory.objects.get(code="VERTRAG")
    r = admin_client.post(
        f"/api/content/file-categories/{kategorie.id}/deaktivieren",
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "INAKTIV"

    # Aus der Standardliste verschwunden, aber nicht gelöscht.
    aktive = {k["code"] for k in admin_client.get("/api/content/file-categories").json()}
    assert "VERTRAG" not in aktive
    assert FileCategory.objects.filter(code="VERTRAG").exists()

    r = admin_client.post(
        f"/api/content/file-categories/{kategorie.id}/aktivieren",
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["status"] == "AKTIV"


@pytest.mark.django_db
def test_systemkategorie_laesst_sich_nicht_deaktivieren(admin_client):
    """Der eigentliche Schutz: `ARTIKELBILD` steht im partiellen UNIQUE-Index
    aus 0042. Deaktiviert liefe der Index still ins Leere."""
    kategorie = FileCategory.objects.get(code="ARTIKELBILD")
    r = admin_client.post(
        f"/api/content/file-categories/{kategorie.id}/deaktivieren",
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "vom Programm vergeben" in r.json()["detail"]


@pytest.mark.django_db
def test_die_datenbank_verbietet_das_umbenennen_einer_systemkategorie():
    """Der Service lässt Code-Änderungen ohnehin nicht zu — die DB ist die
    letzte Instanz, falls je ein anderer Schreibpfad entsteht."""
    from django.db import ProgrammingError, transaction

    kategorie = FileCategory.objects.get(code="BELEG_PDF")
    with pytest.raises(ProgrammingError, match="unveraenderlich"):
        with transaction.atomic():
            FileCategory.objects.filter(pk=kategorie.id).update(code="ANDERS")


@pytest.mark.django_db
def test_die_datenbank_verbietet_das_loeschen():
    """Kein Löschen — alte Dateien tragen ihre Kategorie noch."""
    from django.db import ProgrammingError, transaction

    kategorie = FileCategory.objects.get(code="SCAN")
    with pytest.raises(ProgrammingError, match="append-only"):
        with transaction.atomic():
            FileCategory.objects.filter(pk=kategorie.id).delete()


@pytest.mark.django_db
def test_upload_mit_eigener_kategorie(admin_client, app_user, fake_storage):
    """Der Sinn der Übung: Eine selbst angelegte Kategorie ist benutzbar."""
    from db_core.services import property as property_service

    admin_client.post(
        "/api/content/file-categories",
        data={"label": "Abnahmeprotokoll"},
        content_type="application/json",
    )
    prop = property_service.create_property(
        app_user.id, name="Kategorie-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    datei = dateien_service.datei_hochladen(
        app_user.id,
        dateiname="protokoll.pdf",
        inhalt=b"%PDF-1.4 test",
        link_category="ABNAHMEPROTOKOLL",
        property_id=prop.id,
    )
    assert datei is not None


@pytest.mark.django_db
def test_upload_mit_deaktivierter_kategorie_wird_abgelehnt(admin_client, app_user, fake_storage):
    from db_core.services import property as property_service

    kategorie = FileCategory.objects.get(code="SONSTIGES")
    admin_client.post(
        f"/api/content/file-categories/{kategorie.id}/deaktivieren",
        content_type="application/json",
    )
    prop = property_service.create_property(
        app_user.id, name="Kategorie-Objekt 2", property_type="WEG",
        street="Weg", house_number="2", postal_code="10115", city="Berlin",
    )
    with pytest.raises(ValueError, match="deaktivierte Kategorie"):
        dateien_service.datei_hochladen(
            app_user.id,
            dateiname="x.pdf",
            inhalt=b"%PDF-1.4 test",
            link_category="SONSTIGES",
            property_id=prop.id,
        )
