"""Tests für das Beleg-PDF (services/beleg_pdf.py) mit Firmenprofil-Anschluss.

Kernpunkte: Das PDF zieht den Aussteller aus dem Firmenprofil; ohne gepflegtes
Profil greift ein neutraler Fallback (kein Absturz). Für eine unveröffentlichte
oder fehlende Rechnung gibt es kein PDF (None).
"""
import re
from decimal import Decimal
from hashlib import sha256

import pytest

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import Quote
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


# Kleinstes gültiges 1x1-PNG — von fpdf2/Pillow einbettbar.
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    """In-memory-Objektspeicher (Schnittstelle wie ObjectStorage)."""

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
            raise storage_module.StorageError(f"unbekannt {key}")
        return self.objects[key]

    def remove_object(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    return fake


def _published_invoice(app_user):
    obj = property_service.create_property(
        app_user.id, name="PDF-Objekt", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="Wanda", last_name="WEG")
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
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=weg.id, role=role, is_primary=True
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


@pytest.mark.django_db
def test_pdf_ohne_profil_faellt_sauber_zurueck(app_user):
    """Ohne Firmenprofil erzeugt das PDF trotzdem gültige Bytes (kein Absturz)."""
    inv = _published_invoice(app_user)
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None
    assert data[:4] == b"%PDF"


def test_aussteller_fallback_ohne_profil():
    """Ohne Profil liefert die Aussteller-Zeile den neutralen Fallback."""
    name, subline = beleg_pdf._issuer_lines(None)
    assert name == beleg_pdf._FALLBACK_NAME
    assert beleg_pdf._footer_parts(None) == []


@pytest.mark.django_db
def test_pdf_nutzt_firmenprofil_als_aussteller(app_user):
    inv = _published_invoice(app_user)
    profile, _ = firma_service.update_company_profile(
        app_user.id, company_name="Mitra Sanitär GmbH",
        street="Industriestraße 5", postal_code="12345", city="Musterstadt",
        tax_number="12/345/67890", iban="DE12500105170648489890",
    )
    # Aussteller- und Fußzeilen-Daten stammen aus dem Firmenprofil.
    name, subline = beleg_pdf._issuer_lines(profile)
    assert name == "Mitra Sanitär GmbH"
    assert "Musterstadt" in subline
    parts = beleg_pdf._footer_parts(profile)
    assert any("12/345/67890" in p for p in parts)
    assert any("IBAN DE12500105170648489890" in p for p in parts)
    # Und das vollständige PDF rendert weiterhin zu gültigen Bytes.
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"


# --- Firmenlogo im PDF-Kopf ------------------------------------------------

@pytest.mark.django_db
def test_pdf_bettet_logo_ein(app_user, fake_storage):
    """Ist ein Logo gepflegt, enthält das gerenderte PDF das eingebettete Bild."""
    inv = _published_invoice(app_user)
    firma_service.update_company_profile(app_user.id, company_name="Logo GmbH")
    firma_service.set_company_logo(app_user.id, dateiname="logo.png", inhalt=PNG_1x1)

    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    # fpdf2 legt eingebettete Bilder als Image-XObject ab.
    assert b"/Subtype /Image" in data


@pytest.mark.django_db
def test_pdf_ohne_logo_enthaelt_kein_bild(app_user, fake_storage):
    """Regressionsschutz: ohne Logo bleibt das PDF bildfrei (Text-Kopf unverändert)."""
    inv = _published_invoice(app_user)
    firma_service.update_company_profile(app_user.id, company_name="Ohne Logo GmbH")
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and b"/Subtype /Image" not in data


@pytest.mark.django_db
def test_pdf_ohne_logo_bytegleich_mit_ohne_profilfeld(app_user):
    """Der „kein Logo"-Pfad ist unverändert: dieselbe Rechnung rendert (bis auf
    die eingefrorene Zeit) mit derselben Struktur wie vor dem Logo-Feature —
    insbesondere ohne Bild und ohne Absturz, auch wenn nie ein Storage berührt wird."""
    inv = _published_invoice(app_user)
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    assert b"/Subtype /Image" not in data


@pytest.mark.django_db
def test_pdf_logo_gesetzt_aber_storage_weg_rendert_ohne_logo(app_user, monkeypatch):
    """Logo ist gesetzt, der Objektspeicher aber nicht erreichbar → das PDF
    rendert trotzdem (ohne Logo), kein Absturz."""
    # Logo mit funktionierendem Speicher setzen …
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    inv = _published_invoice(app_user)
    firma_service.update_company_profile(app_user.id, company_name="Degrad GmbH")
    firma_service.set_company_logo(app_user.id, dateiname="logo.png", inhalt=PNG_1x1)

    # … dann den Speicher „ausschalten".
    def boom():
        raise storage_module.StorageError("MinIO weg")

    monkeypatch.setattr(storage_module, "get_storage", boom)
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"
    assert b"/Subtype /Image" not in data


@pytest.mark.django_db
def test_pdf_kaputtes_logo_rendert_ohne_absturz(app_user, monkeypatch):
    """Sind die Logo-Bytes kein gültiges Bild, wird das Logo übersprungen — das
    PDF darf nie an einem Logo scheitern."""
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    inv = _published_invoice(app_user)
    firma_service.update_company_profile(app_user.id, company_name="Kaputt GmbH")
    # Magic-Bytes gaukeln PNG vor, der Rest ist Müll → fpdf2/Pillow scheitert am
    # Einbetten, _place_logo fängt das ab.
    firma_service.set_company_logo(
        app_user.id, dateiname="logo.png", inhalt=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    )
    data = beleg_pdf.render_invoice_pdf(inv.id)
    assert data is not None and data[:4] == b"%PDF"


@pytest.mark.django_db
def test_pdf_nur_fuer_veroeffentlichte(app_user):
    obj = property_service.create_property(
        app_user.id, name="Entwurf-Objekt", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Z", "quantity": 1,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    assert beleg_pdf.render_invoice_pdf(inv.id) is None


# --- Angebots-PDF (invoicing.quote) ----------------------------------------

def _entwurf_quote(app_user, *, with_order=True, recipient_role="INVOICE_RECIPIENT",
                   with_email=True):
    """Angebots-Entwurf (ENTWURF) inkl. optionalem Auftrag mit Empfänger-Beteiligtem."""
    obj = property_service.create_property(
        app_user.id, name="Angebots-Objekt", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    weg = identity_service.create_person(
        app_user.id, first_name="Quintus", last_name="Quote"
    )
    if with_email:
        identity_service.add_contact_point(
            app_user.id, weg.id, contact_type="EMAIL",
            value="angebot-kunde@example.test", is_primary=True,
        )
    order = None
    if with_order:
        order = auftrag_service.create_work_order(
            app_user.id, property_id=obj.id, title="Auftrag zum Angebot"
        )
        if recipient_role is not None:
            auftrag_service.add_work_order_party(
                app_user.id, work_order_id=order.id, party_id=weg.id,
                role=recipient_role, is_primary=True,
            )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Bad sanieren",
        lines=[{"line_type": "MATERIAL", "description": "Fliesen", "quantity": 20,
                "unit": "m2", "unit_price": "34.50", "tax_code": "DE_19"}],
    )
    if order is not None:
        with business_transaction(app_user.id):
            Quote.objects.filter(id=quote.id).update(work_order_id=order.id)
    quote.refresh_from_db()
    return quote, weg


def _sent_quote(app_user, **kw):
    quote, weg = _entwurf_quote(app_user, **kw)
    beleg_service.send_quote(app_user.id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote, weg


@pytest.mark.django_db
def test_quote_pdf_ab_versendet(app_user):
    """Ein versendetes Angebot rendert zu gültigen PDF-Bytes."""
    quote, _ = _sent_quote(app_user)
    assert quote.status == "VERSENDET"
    data = beleg_pdf.render_quote_pdf(quote.id)
    assert data is not None and data[:4] == b"%PDF"


@pytest.mark.django_db
def test_quote_pdf_entwurf_liefert_kein_finales_pdf(app_user):
    """Ein Entwurf täuscht kein finales PDF vor (None → 404 in der API)."""
    quote, _ = _entwurf_quote(app_user)
    assert quote.status == "ENTWURF"
    assert beleg_pdf.render_quote_pdf(quote.id) is None


@pytest.mark.django_db
def test_quote_pdf_unbekannt_none(app_user):
    import uuid as _uuid
    assert beleg_pdf.render_quote_pdf(_uuid.uuid4()) is None


@pytest.mark.django_db
def test_quote_empfaenger_ableitung_invoice_recipient(app_user):
    """Empfänger wird über work_order_party INVOICE_RECIPIENT abgeleitet."""
    quote, weg = _sent_quote(app_user, recipient_role="INVOICE_RECIPIENT")
    q = (
        Quote.objects.filter(id=quote.id)
        .select_related("work_order")
        .prefetch_related("work_order__parties__party")
        .first()
    )
    party = beleg_pdf.quote_recipient_party(q)
    assert party is not None and party.id == weg.id


@pytest.mark.django_db
def test_quote_empfaenger_fallback_principal(app_user):
    """Ohne INVOICE_RECIPIENT greift PRINCIPAL als Ersatz."""
    quote, weg = _sent_quote(app_user, recipient_role="PRINCIPAL")
    q = (
        Quote.objects.filter(id=quote.id)
        .select_related("work_order")
        .prefetch_related("work_order__parties__party")
        .first()
    )
    party = beleg_pdf.quote_recipient_party(q)
    assert party is not None and party.id == weg.id


@pytest.mark.django_db
def test_quote_pdf_ohne_auftrag_rendert_ohne_empfaenger(app_user):
    """Ein Angebot OHNE Auftrag/Empfänger rendert trotzdem (kein formaler Adressat)."""
    quote, _ = _sent_quote(app_user, with_order=False, with_email=False)
    q = Quote.objects.filter(id=quote.id).select_related("work_order").first()
    assert beleg_pdf.quote_recipient_party(q) is None
    data = beleg_pdf.render_quote_pdf(quote.id)
    assert data is not None and data[:4] == b"%PDF"
