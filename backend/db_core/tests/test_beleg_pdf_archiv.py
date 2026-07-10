"""Tests für die GoBD-Archivierung des Beleg-PDF (services/beleg_pdf.py).

Kernpunkte:
- Erster Abruf archiviert die Ausfertigung (content.file + content.file_link,
  link_category='BELEG_PDF') und legt das Objekt in den Speicher.
- Jeder weitere Abruf liefert DIESELBE archivierte Datei aus dem Speicher, nicht
  neu gerendert (GoBD: eine Ausfertigung).
- Wettlauf zweier paralleler Erstabrufe (Finding P-1): Verliert einer den
  partiellen UNIQUE-Index (IntegrityError), wird die Gewinner-Datei
  nachselektiert und ausgeliefert — kein 500, weiterhin genau ein file_link.
- Ist der Objektspeicher nicht erreichbar, bleibt der Beleg zugänglich
  (on-the-fly) und die Archivierung fällt aus.

Diese Tests brauchen KEIN echtes MinIO: der Objektspeicher wird durch einen
in-memory-Fake ersetzt (monkeypatch). Der echte End-to-End-Lauf gegen MinIO
steht in test_storage_minio_e2e.py und überspringt sauber ohne Server.
"""
from hashlib import sha256

import pytest

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import Quote
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from django.db import connection


class FakeStorage:
    """In-memory-Objektspeicher mit derselben Schnittstelle wie ObjectStorage."""

    def __init__(self):
        self.objects = {}
        self.removed = []

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
        self.removed.append(key)
        self.objects.pop(key, None)


def _count_beleg_links(invoice_id):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM content.file_link "
            "WHERE invoice_id = %s AND link_category = 'BELEG_PDF'",
            [str(invoice_id)],
        )
        return cur.fetchone()[0]


def _published_invoice(app_user):
    obj = property_service.create_property(
        app_user.id, name="Archiv-Objekt", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="Anna", last_name="Archiv")
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag zur Rechnung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=weg.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id, role=role, is_primary=True
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


def _count_quote_links(quote_id):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM content.file_link "
            "WHERE quote_id = %s AND link_category = 'BELEG_PDF'",
            [str(quote_id)],
        )
        return cur.fetchone()[0]


def _sent_quote(app_user):
    obj = property_service.create_property(
        app_user.id, name="Archiv-Angebot", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Bad sanieren",
        lines=[{"line_type": "MATERIAL", "description": "Fliesen", "quantity": 20,
                "unit": "m2", "unit_price": "34.50", "tax_code": "DE_19"}],
    )
    beleg_service.send_quote(app_user.id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    return fake


@pytest.mark.django_db
def test_erster_abruf_archiviert(app_user, fake_storage):
    inv = _published_invoice(app_user)
    pdf = beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    assert pdf is not None and pdf[:4] == b"%PDF"

    key = beleg_pdf._archived_storage_key(inv.id)
    assert key is not None
    # Genau ein Objekt im Speicher, byte-identisch zum Rückgabewert.
    assert fake_storage.objects[key] == pdf
    assert _count_beleg_links(inv.id) == 1
    # content.file trägt sha256 + Größe der abgelegten Bytes.
    with connection.cursor() as cur:
        cur.execute(
            "SELECT f.sha256, f.size_bytes, f.mime_type FROM content.file f "
            "JOIN content.file_link fl ON fl.file_id = f.id "
            "WHERE fl.invoice_id = %s AND fl.link_category = 'BELEG_PDF'",
            [str(inv.id)],
        )
        digest, size, mime = cur.fetchone()
    assert digest == sha256(pdf).hexdigest()
    assert size == len(pdf)
    assert mime == "application/pdf"


@pytest.mark.django_db
def test_zweiter_abruf_liefert_dieselbe_datei(app_user, fake_storage):
    inv = _published_invoice(app_user)
    first = beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    key1 = beleg_pdf._archived_storage_key(inv.id)

    # Zweiter Abruf: kommt aus dem Speicher (nicht neu gerendert) → exakt gleich.
    second = beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    key2 = beleg_pdf._archived_storage_key(inv.id)
    assert key2 == key1
    assert second == fake_storage.objects[key1]
    assert second == first
    # Weiterhin genau EIN file_link und EIN Objekt (keine Zweitausfertigung).
    assert _count_beleg_links(inv.id) == 1
    assert len(fake_storage.objects) == 1


@pytest.mark.django_db
def test_race_wird_nachselektiert(app_user, fake_storage, monkeypatch):
    """Finding P-1: zweiter „Erstabruf" verliert den UNIQUE-Index und liefert
    die vom Gewinner archivierte Datei aus (Nachselektion), kein 500."""
    inv = _published_invoice(app_user)
    # Gewinner archiviert regulär.
    winner = beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    winner_key = beleg_pdf._archived_storage_key(inv.id)

    # Fast-Path-Check beim ersten Aufruf täuschen: so tun, als sei noch nichts
    # archiviert → der Schreibpfad läuft und der file_link-INSERT kollidiert mit
    # dem partiellen UNIQUE-Index (Migration 0032) → IntegrityError → Nachselektion.
    real_lookup = beleg_pdf._archived_storage_key
    calls = {"n": 0}

    def flaky_lookup(invoice_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_lookup(invoice_id)

    monkeypatch.setattr(beleg_pdf, "_archived_storage_key", flaky_lookup)

    served = beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    monkeypatch.undo()

    # Ausgeliefert wird die Gewinner-Datei; der Verlierer-Upload wurde entfernt.
    assert served == fake_storage.objects[winner_key]
    assert served == winner
    assert _count_beleg_links(inv.id) == 1
    assert fake_storage.removed  # Verlierer-Objekt best-effort abgeräumt
    # Nur noch das Gewinner-Objekt liegt im Speicher.
    assert set(fake_storage.objects) == {winner_key}


@pytest.mark.django_db
def test_entwurf_wird_nicht_archiviert(app_user, fake_storage):
    """Ein Entwurf bekommt kein PDF (None) und wird nicht archiviert."""
    obj = property_service.create_property(
        app_user.id, name="Entwurf", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Z", "quantity": 1,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    assert beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id) is None
    assert _count_beleg_links(inv.id) == 0
    assert fake_storage.objects == {}


@pytest.mark.django_db
def test_degradiert_ohne_objektspeicher(app_user, monkeypatch):
    """Objektspeicher nicht erreichbar → Beleg bleibt zugänglich (on-the-fly),
    keine Archivierung, kein Absturz."""
    inv = _published_invoice(app_user)

    def boom():
        raise storage_module.StorageError("MinIO nicht erreichbar")

    monkeypatch.setattr(storage_module, "get_storage", boom)
    pdf = beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    assert pdf is not None and pdf[:4] == b"%PDF"
    # Nichts archiviert (Speicher war weg) → die Archivierung wird beim nächsten
    # Abruf mit funktionierendem Speicher nachgeholt.
    assert _count_beleg_links(inv.id) == 0


@pytest.mark.django_db
def test_quote_erster_abruf_archiviert(app_user, fake_storage):
    """Das Angebots-PDF wird beim Erstabruf über quote_id archiviert."""
    quote = _sent_quote(app_user)
    pdf = beleg_pdf.get_or_archive_quote_pdf(app_user.id, quote.id)
    assert pdf is not None and pdf[:4] == b"%PDF"

    key = beleg_pdf._archived_quote_storage_key(quote.id)
    assert key is not None
    assert fake_storage.objects[key] == pdf
    assert _count_quote_links(quote.id) == 1
    # storage_key liegt im Angebots-Präfix (nicht im Rechnungs-Präfix).
    assert key.startswith("belege/angebot/")


@pytest.mark.django_db
def test_quote_zweiter_abruf_liefert_dieselbe_datei(app_user, fake_storage):
    quote = _sent_quote(app_user)
    first = beleg_pdf.get_or_archive_quote_pdf(app_user.id, quote.id)
    key1 = beleg_pdf._archived_quote_storage_key(quote.id)
    second = beleg_pdf.get_or_archive_quote_pdf(app_user.id, quote.id)
    key2 = beleg_pdf._archived_quote_storage_key(quote.id)
    assert key2 == key1
    assert second == fake_storage.objects[key1] == first
    assert _count_quote_links(quote.id) == 1
    assert len(fake_storage.objects) == 1


@pytest.mark.django_db
def test_quote_entwurf_wird_nicht_archiviert(app_user, fake_storage):
    """Ein Angebots-Entwurf bekommt kein PDF (None) und wird nicht archiviert."""
    obj = property_service.create_property(
        app_user.id, name="Entwurf-Angebot", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Entwurf",
        lines=[{"line_type": "MATERIAL", "description": "Z", "quantity": 1,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    assert beleg_pdf.get_or_archive_quote_pdf(app_user.id, quote.id) is None
    assert _count_quote_links(quote.id) == 0
    assert fake_storage.objects == {}


@pytest.mark.django_db
def test_quote_degradiert_ohne_objektspeicher(app_user, monkeypatch):
    """Objektspeicher weg → Angebot bleibt zugänglich (on-the-fly), keine Archivierung."""
    quote = _sent_quote(app_user)

    def boom():
        raise storage_module.StorageError("MinIO nicht erreichbar")

    monkeypatch.setattr(storage_module, "get_storage", boom)
    pdf = beleg_pdf.get_or_archive_quote_pdf(app_user.id, quote.id)
    assert pdf is not None and pdf[:4] == b"%PDF"
    assert _count_quote_links(quote.id) == 0


@pytest.mark.django_db
def test_archiviert_aber_objekt_weg_degradiert(app_user, monkeypatch):
    """Steckbrief existiert, Objekt aber (vorübergehend) nicht abrufbar →
    on-the-fly ausliefern statt 500, ohne den Link zu duplizieren."""
    inv = _published_invoice(app_user)
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    assert _count_beleg_links(inv.id) == 1

    # Objekt aus dem Speicher entfernen (Steckbrief bleibt): nächster Abruf muss
    # degradieren, nicht abstürzen und keinen zweiten Link anlegen.
    fake.objects.clear()
    pdf = beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    assert pdf is not None and pdf[:4] == b"%PDF"
    assert _count_beleg_links(inv.id) == 1
