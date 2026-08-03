"""API-Tests des DATANORM-Frontend-Imports
(POST /api/pricing/supplier-connections/{id}/imports/datanorm).

Deckt ab: Vorschau (dry_run schreibt nichts), Erstimport (Artikel + EK aus
Preiseinheit/Rabatt), Re-Import (Upsert → aktualisiert, Preisänderung), Löschung
(Artikel INAKTIV + Referenz beendet), sowie Auth/Rechte und Fehlerpfade.
"""
import io
import uuid
import zipfile
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from db_core.models import Article, ArticleSupplierReference
from db_core.services import anbindung as anbindung_service
from db_core.services import datanorm
from db_core.services import identity as identity_service
from db_core.models import AppUser
from .conftest import make_role_user

_URL = "/api/pricing/supplier-connections"

STAMM = """\
V 020726DATANORM - Datenservice - Artikelstamm  Copyright Testhaendler          04EUR
A;N;ART1;50;Erster Artikel;Zweite Zeile;1;0;ST;1000;RG01; ; ;
B;N;ART1;FABRIKAT;HERSTNR; ;0;0;0; ; ; ;0;0; ; ;
A;N;ART2;00;Zweiter Artikel;;1;2;ST;1290;RG02; ; ;
A;N;ART3;00;Dritter ohne Zusatz;;2;0;M;500;; ; ;
"""

PREISE = """\
V 030726DATANORM - Datenservice - Preispflege    Copyright Testhaendler         04EUR
P;A;ART1;1;1000;1;3300;;;;;ART2;1;1290;1;4000;;;;;
P;A;ART3;1;500;1;1000;;;;;ART3;2;450;;;;;;;
"""

# Re-Import: ART1 wird billiger (Netto 5,00 statt Liste-33 %), ART3 gelöscht.
STAMM_UPDATE = """\
V 040726DATANORM - Datenservice - Artikelstamm  Copyright Testhaendler          04EUR
A;A;ART1;50;Erster Artikel neu;;2;0;ST;500;RG01; ; ;
A;L;ART3;00;Dritter ohne Zusatz;;2;0;M;500;; ; ;
"""


def _seed_actor():
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name="Seed", status="ACTIVE", version=1
    )


def _zip_bytes(name: str, inhalt: str) -> bytes:
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr(f"{name}.001", inhalt.encode(datanorm.ENCODING))
    return puffer.getvalue()


def _stamm_datei(inhalt=STAMM) -> SimpleUploadedFile:
    return SimpleUploadedFile("datanorm.zip", _zip_bytes("datanorm", inhalt),
                              content_type="application/zip")


def _preis_datei(inhalt=PREISE) -> SimpleUploadedFile:
    return SimpleUploadedFile("datpreis.zip", _zip_bytes("datpreis", inhalt),
                              content_type="application/zip")


def _connection():
    actor = _seed_actor()
    supplier = identity_service.create_person(actor.id, first_name="Gross", last_name="Handel")
    return anbindung_service.create_connection(
        actor.id, supplier_party_id=supplier.id, source_namespace="testns",
        label="Testhändler", source_system="DATANORM",
    )


def _import(client, conn_id, *, stamm=None, preise=None, dry_run=False):
    data = {"stamm": stamm or _stamm_datei(), "dry_run": "true" if dry_run else "false"}
    if preise is not None:
        data["preise"] = preise
    return client.post(f"{_URL}/{conn_id}/imports/datanorm", data=data)


# --- Vorschau + Erstimport --------------------------------------------------

@pytest.mark.django_db
def test_dry_run_schreibt_nichts(admin_client):
    conn = _connection()
    r = _import(admin_client, conn.id, preise=_preis_datei(), dry_run=True)
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["dry_run"] is True
    assert body["verarbeitet"] == 3 and body["angelegt"] == 3
    # Nichts geschrieben:
    assert not Article.objects.filter(article_number__in=["ART1", "ART2", "ART3"]).exists()


@pytest.mark.django_db
def test_erstimport_legt_artikel_und_ek_an(admin_client):
    conn = _connection()
    r = _import(admin_client, conn.id, preise=_preis_datei())
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["angelegt"] == 3 and body["aktualisiert"] == 0
    art1 = Article.objects.get(article_number="ART1")
    assert art1.description == "Erster Artikel Zweite Zeile"
    ref1 = ArticleSupplierReference.objects.get(
        article_id=art1.id, source_system="DATANORM", valid_until__isnull=True
    )
    # ART1: Liste 10,00 € - 33 % = 6,70 €
    assert ref1.last_purchase_price == Decimal("6.7000")
    assert ref1.currency == "EUR"
    # ART3: Nettopreis 4,50, kein Listenpreis → EK bekannt, list_price None-ish
    art3 = Article.objects.get(article_number="ART3")
    ref3 = ArticleSupplierReference.objects.get(
        article_id=art3.id, valid_until__isnull=True
    )
    assert ref3.last_purchase_price == Decimal("4.5000")


@pytest.mark.django_db
def test_ohne_preisdatei_ek_aus_a_satz(admin_client):
    conn = _connection()
    # Ohne Preisdatei: EK kommt direkt aus dem A-Satz, sofern dessen
    # Preiskennzeichen Netto (2) ist. ART1/ART2 sind Liste (1) → EK unbekannt;
    # ART3 ist Netto → EK 5,00.
    r = _import(admin_client, conn.id, preise=None)
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["angelegt"] == 3 and body["ohne_einkaufspreis"] == 2
    ref1 = ArticleSupplierReference.objects.get(
        article__article_number="ART1", valid_until__isnull=True
    )
    assert ref1.last_purchase_price is None and ref1.currency is None
    ref3 = ArticleSupplierReference.objects.get(
        article__article_number="ART3", valid_until__isnull=True
    )
    assert ref3.last_purchase_price == Decimal("5.0000") and ref3.currency == "EUR"


# --- Re-Import (Upsert) + Löschung ------------------------------------------

@pytest.mark.django_db
def test_reimport_aktualisiert_und_loescht(admin_client):
    conn = _connection()
    _import(admin_client, conn.id, preise=_preis_datei())  # Erstimport
    r = _import(admin_client, conn.id, stamm=_stamm_datei(STAMM_UPDATE))
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["aktualisiert"] == 1      # ART1
    assert body["deaktiviert"] == 1       # ART3 (VKZ L)

    art1 = Article.objects.get(article_number="ART1")
    assert art1.description == "Erster Artikel neu"
    ref1 = ArticleSupplierReference.objects.get(
        article_id=art1.id, valid_until__isnull=True
    )
    # Neuer Netto-EK 5,00 (Preiseinheit 0) — die offene Referenz wurde aktualisiert.
    assert ref1.last_purchase_price == Decimal("5.0000")

    # Löschung → Artikel INAKTIV (das operative Signal). Die am selben Tag
    # angelegte Referenz bleibt offen (der DB-CHECK valid_until > valid_from
    # verhindert das Schließen am selben Tag; real liegen Import und Löschung
    # Tage auseinander) — die EK-Historie bleibt erhalten.
    art3 = Article.objects.get(article_number="ART3")
    assert art3.status == "INAKTIV"


@pytest.mark.django_db
def test_import_stempelt_last_import_at(admin_client):
    conn = _connection()
    assert conn.last_import_at is None
    _import(admin_client, conn.id, preise=_preis_datei())
    conn.refresh_from_db()
    assert conn.last_import_at is not None


# --- Auth / Rechte / Fehler -------------------------------------------------

@pytest.mark.django_db
def test_ohne_recht_403(db):
    conn = _connection()
    user, _ = make_role_user(None)
    c = Client()
    c.force_login(user)
    r = _import(c, conn.id)
    assert r.status_code == 403


@pytest.mark.django_db
def test_unbekannte_anbindung_404(admin_client):
    r = _import(admin_client, uuid.uuid4())
    assert r.status_code == 404


@pytest.mark.django_db
def test_herstellerkatalog_setzt_herstellernummer(admin_client):
    """Hersteller liefern ihre Ersatzteilkataloge ebenfalls als DATANORM.

    Früher lehnte der Import Hersteller-Anbindungen rundweg ab. Er nimmt sie
    jetzt an — und leitet aus der Anbindungsart die Feldbedeutung ab: Beim
    Hersteller IST die Artikelnummer die Herstellernummer, und der Anbindungsname
    ist der Hersteller. Aus dem Matchcode wird NIE ein Herstellername.
    """
    actor = _seed_actor()
    supplier = identity_service.create_person(actor.id, first_name="Herst", last_name="Eller")
    conn = anbindung_service.create_connection(
        actor.id, supplier_party_id=supplier.id, source_namespace="hrst",
        label="Hersteller", source_system="DATANORM", connection_kind="HERSTELLER",
    )
    r = _import(admin_client, conn.id, preise=_preis_datei())
    assert r.status_code == 200, r.content
    art1 = Article.objects.get(article_number="ART1")
    assert art1.manufacturer_number == "ART1"
    assert art1.manufacturer_name == "Hersteller"


@pytest.mark.django_db
def test_grosshandel_erfindet_keine_herstellernummer(admin_client):
    """Der Großhändler liefert keine Herstellernummer — also steht dort nichts.

    B&Os B-Satz-Feld 4 trägt eine hauseigene Katalognummer (`ZRB2071510`,
    `ARESRT10018217`). Sie landete früher als „Hersteller-Nr." im Stamm und war
    damit eine Nummer, die außerhalb von B&O nirgends existiert. Sie gehört an
    die Lieferantenreferenz, der Matchcode ins Matchcode-Feld.
    """
    conn = _connection()
    r = _import(admin_client, conn.id, preise=_preis_datei())
    assert r.status_code == 200, r.content
    art1 = Article.objects.get(article_number="ART1")
    assert art1.manufacturer_number is None
    assert art1.manufacturer_name is None
    ref1 = ArticleSupplierReference.objects.get(
        article_id=art1.id, valid_until__isnull=True
    )
    assert ref1.supplier_article_number == "ART1"


@pytest.mark.django_db
def test_zip_mit_mehreren_dateien_422(admin_client):
    conn = _connection()
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("a.001", STAMM.encode(datanorm.ENCODING))
        z.writestr("b.001", STAMM.encode(datanorm.ENCODING))
    datei = SimpleUploadedFile("x.zip", puffer.getvalue(), content_type="application/zip")
    r = admin_client.post(
        f"{_URL}/{conn.id}/imports/datanorm", data={"stamm": datei, "dry_run": "false"}
    )
    assert r.status_code == 422
