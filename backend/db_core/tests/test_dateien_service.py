"""Datei-Ablage: Upload, Verknüpfung, Deduplizierung — und die Sicherheitsregeln.

Der Objektspeicher wird durch einen In-Memory-Fake ersetzt (Muster aus
test_beleg_pdf_archiv.py); MinIO muss für diese Tests nicht laufen.

Die drei Regeln, die hier zählen:
  * Der Dateiname bestimmt niemals den Speicherort (Path Traversal strukturell
    ausgeschlossen, nicht durch Filterung).
  * Der Dateityp kommt aus einer Whitelist, nicht aus dem Client-Header. Kein
    HTML, kein SVG — beide können Skripte im Kontext der Anwendung ausführen.
  * Eine Datei hängt an genau einem Objekt (DB-CHECK).
"""
import uuid
from hashlib import sha256

import pytest

from db_core import storage as storage_module
from db_core.models import File, FileLink
from db_core.services import dateien
from db_core.services import identity as identity_service
from db_core.services import property as property_service

PDF = b"%PDF-1.4 Testinhalt"


class FakeStorage:
    """In-memory-Objektspeicher mit derselben Schnittstelle wie ObjectStorage."""

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
            raise storage_module.StorageError(f"unbekanntes Objekt {key}")
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
        app_user.id, name="Dateiobjekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    from db_core.services import projekt as projekt_service

    return projekt_service.create_project(
        app_user.id, name="Dateiprojekt", property_ids=[obj.id]
    )


# --- Upload und Verknüpfung -------------------------------------------------

@pytest.mark.django_db
def test_upload_an_projekt(app_user, projekt, fake_storage):
    datei, link = dateien.datei_hochladen(
        app_user.id, dateiname="Angebot.pdf", inhalt=PDF, project_id=projekt.id
    )
    assert datei.original_filename == "Angebot.pdf"
    assert datei.mime_type == "application/pdf"
    assert datei.size_bytes == len(PDF)
    assert datei.sha256 == sha256(PDF).hexdigest()
    assert link.project_id == projekt.id
    assert link.link_category == "DOKUMENT"
    # Der Inhalt liegt wirklich im Objektspeicher.
    assert fake_storage.objects[datei.storage_key] == PDF


@pytest.mark.django_db
def test_upload_an_kontakt_und_liegenschaft(app_user, fake_storage):
    person = identity_service.create_person(app_user.id, first_name="A", last_name="B")
    obj = property_service.create_property(
        app_user.id, name="Haus", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    _, l1 = dateien.datei_hochladen(
        app_user.id, dateiname="scan.pdf", inhalt=b"a", party_id=person.id,
        link_category="SCAN",
    )
    _, l2 = dateien.datei_hochladen(
        app_user.id, dateiname="plan.pdf", inhalt=b"b", property_id=obj.id,
        link_category="PLAN",
    )
    assert l1.party_id == person.id and l1.project_id is None
    assert l2.property_id == obj.id


@pytest.mark.django_db
def test_gleicher_inhalt_wird_nur_einmal_gespeichert(app_user, projekt, fake_storage):
    """Dieselbe Datei an zwei Orten ist derselbe Inhalt, nicht zwei Kopien."""
    person = identity_service.create_person(app_user.id, first_name="A", last_name="B")
    d1, l1 = dateien.datei_hochladen(
        app_user.id, dateiname="rechnung.pdf", inhalt=PDF, project_id=projekt.id
    )
    d2, l2 = dateien.datei_hochladen(
        app_user.id, dateiname="rechnung.pdf", inhalt=PDF, party_id=person.id
    )
    assert d1.id == d2.id                    # eine Datei
    assert l1.id != l2.id                    # zwei Verknüpfungen
    assert File.objects.filter(sha256=d1.sha256).count() == 1
    assert len(fake_storage.objects) == 1


# --- Sicherheit -------------------------------------------------------------

@pytest.mark.django_db
def test_dateiname_bestimmt_nie_den_speicherort(app_user, projekt, fake_storage):
    """Ein Upload namens '../../etc/passwd' landet unter upload/<uuid>."""
    datei, _ = dateien.datei_hochladen(
        app_user.id, dateiname="../../etc/passwd.pdf", inhalt=PDF,
        project_id=projekt.id,
    )
    assert datei.original_filename == "passwd.pdf"     # nur der Basisname
    assert datei.storage_key.startswith("upload/")
    assert ".." not in datei.storage_key
    uuid.UUID(datei.storage_key.split("/", 1)[1])      # der Rest ist eine UUID


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name",
    ["schad.exe", "seite.html", "bild.svg", "skript.js", "makro.docm", "ohneendung"],
)
def test_nicht_zugelassene_dateitypen(app_user, projekt, fake_storage, name):
    """HTML und SVG koennen Skripte im Kontext der Anwendung ausfuehren."""
    with pytest.raises(dateien.DateiFehler, match="nicht zugelassen"):
        dateien.datei_hochladen(
            app_user.id, dateiname=name, inhalt=b"x", project_id=projekt.id
        )


@pytest.mark.django_db
def test_endung_zaehlt_nicht_die_behauptung_des_clients(app_user, projekt, fake_storage):
    """Der MIME-Typ wird aus der Endung abgeleitet, nicht uebernommen."""
    datei, _ = dateien.datei_hochladen(
        app_user.id, dateiname="tabelle.xlsx", inhalt=b"PK\x03\x04",
        project_id=projekt.id,
    )
    assert datei.mime_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@pytest.mark.django_db
def test_leere_datei(app_user, projekt, fake_storage):
    with pytest.raises(dateien.DateiFehler, match="leer"):
        dateien.datei_hochladen(
            app_user.id, dateiname="leer.pdf", inhalt=b"", project_id=projekt.id
        )


@pytest.mark.django_db
def test_zu_grosse_datei(app_user, projekt, fake_storage, monkeypatch):
    monkeypatch.setattr(dateien, "MAX_BYTES", 10)
    with pytest.raises(dateien.DateiFehler, match="zu groß"):
        dateien.datei_hochladen(
            app_user.id, dateiname="gross.pdf", inhalt=b"x" * 11, project_id=projekt.id
        )


@pytest.mark.django_db
def test_genau_ein_ziel(app_user, projekt, fake_storage):
    person = identity_service.create_person(app_user.id, first_name="A", last_name="B")
    with pytest.raises(dateien.DateiFehler, match="genau einem Objekt"):
        dateien.datei_hochladen(
            app_user.id, dateiname="x.pdf", inhalt=PDF,
            project_id=projekt.id, party_id=person.id,
        )
    with pytest.raises(dateien.DateiFehler, match="genau einem Objekt"):
        dateien.datei_hochladen(app_user.id, dateiname="x.pdf", inhalt=PDF)


@pytest.mark.django_db
def test_unbekanntes_ziel(app_user, fake_storage):
    with pytest.raises(dateien.DateiFehler, match="Unbekanntes Ziel"):
        dateien.datei_hochladen(
            app_user.id, dateiname="x.pdf", inhalt=PDF, quatsch_id=uuid.uuid4()
        )


@pytest.mark.django_db
def test_unbekannte_kategorie(app_user, projekt, fake_storage):
    with pytest.raises(dateien.DateiFehler, match="Kategorie"):
        dateien.datei_hochladen(
            app_user.id, dateiname="x.pdf", inhalt=PDF, project_id=projekt.id,
            link_category="ERFUNDEN",
        )


# --- Lesen und Lösen --------------------------------------------------------

@pytest.mark.django_db
def test_inhalt_lesen(app_user, projekt, fake_storage):
    datei, _ = dateien.datei_hochladen(
        app_user.id, dateiname="a.pdf", inhalt=PDF, project_id=projekt.id
    )
    gelesen, bytes_ = dateien.datei_inhalt(datei.id)
    assert gelesen.id == datei.id
    assert bytes_ == PDF


@pytest.mark.django_db
def test_inhalt_unbekannt(app_user, fake_storage):
    with pytest.raises(dateien.DateiFehler, match="nicht gefunden"):
        dateien.datei_inhalt(uuid.uuid4())


@pytest.mark.django_db
def test_dateien_am_ziel(app_user, projekt, fake_storage):
    dateien.datei_hochladen(
        app_user.id, dateiname="a.pdf", inhalt=b"a", project_id=projekt.id
    )
    dateien.datei_hochladen(
        app_user.id, dateiname="b.pdf", inhalt=b"b", project_id=projekt.id
    )
    links = dateien.dateien_am_ziel(project_id=projekt.id)
    assert links.count() == 2
    assert {l.file.original_filename for l in links} == {"a.pdf", "b.pdf"}


@pytest.mark.django_db
def test_verknuepfung_loesen_laesst_datei_bestehen(app_user, projekt, fake_storage):
    """Die Verknüpfung geht, die Datei bleibt — sie ist unveränderlich."""
    datei, link = dateien.datei_hochladen(
        app_user.id, dateiname="a.pdf", inhalt=PDF, project_id=projekt.id
    )
    dateien.verknuepfung_loesen(app_user.id, link_id=link.id)
    assert not FileLink.objects.filter(id=link.id).exists()
    assert File.objects.filter(id=datei.id).exists()
    assert fake_storage.objects[datei.storage_key] == PDF


@pytest.mark.django_db
def test_datei_ist_physisch_unveraenderlich(app_user, projekt, fake_storage):
    """trg_file_immutable: kein UPDATE, kein DELETE auf content.file."""
    from django.db import Error, transaction

    from db_core.db_context import business_transaction

    datei, _ = dateien.datei_hochladen(
        app_user.id, dateiname="a.pdf", inhalt=PDF, project_id=projekt.id
    )
    with pytest.raises(Error):
        with transaction.atomic():
            with business_transaction(app_user.id):
                File.objects.filter(id=datei.id).update(original_filename="neu.pdf")
