"""Tests für das Beleg-PDF (services/beleg_pdf.py) mit Firmenprofil-Anschluss.

Kernpunkte: Das PDF zieht den Aussteller aus dem Firmenprofil; ohne gepflegtes
Profil greift ein neutraler Fallback (kein Absturz). Für eine unveröffentlichte
oder fehlende Rechnung gibt es kein PDF (None).
"""
import re
from decimal import Decimal

import pytest

from db_core.db_context import business_transaction
from db_core.models import Quote
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


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
