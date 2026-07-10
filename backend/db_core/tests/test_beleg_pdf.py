"""Tests für das Beleg-PDF (services/beleg_pdf.py) mit Firmenprofil-Anschluss.

Kernpunkte: Das PDF zieht den Aussteller aus dem Firmenprofil; ohne gepflegtes
Profil greift ein neutraler Fallback (kein Absturz). Für eine unveröffentlichte
oder fehlende Rechnung gibt es kein PDF (None).
"""
import re
from decimal import Decimal

import pytest

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
