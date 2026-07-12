"""API-Tests für die E-Rechnung (ZUGFeRD/Factur-X).

Endpunkte:
  GET /api/invoicing/invoices/{id}/zugferd.pdf  → Hybrid-PDF (PDF/A-3B + CII-XML)
  GET /api/invoicing/invoices/{id}/zugferd.xml  → das reine CII-XML

Beide verlangen `invoicing/LESEN` (wie das Beleg-PDF), gibt es nur für
VEROEFFENTLICHT (sonst 404) und antworten mit 422, wenn die Datenlage kein
gültiges EN16931-XML hergibt (z. B. ohne Firmenprofil).
"""
import uuid

import pytest
from facturx import get_xml_from_pdf

from db_core import storage as storage_module
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.tests.test_erechnung_service import FakeStorage

PDF_URL = "/api/invoicing/invoices/{}/zugferd.pdf"
XML_URL = "/api/invoicing/invoices/{}/zugferd.xml"


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    return fake


@pytest.fixture
def akteur(db):
    from api.tests.conftest import make_app_user
    return make_app_user("E-Rechnung-Akteur")


def _firmenprofil(akteur):
    firma_service.update_company_profile(
        akteur.id, company_name="Mitra Sanitär GmbH",
        street="Industriestraße 5", postal_code="12345", city="Musterstadt",
        vat_id="DE123456789", iban="DE02120300000000202051",
    )


def _entwurf(akteur):
    obj = property_service.create_property(
        akteur.id, name="E-Rechnungs-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_organization(
        akteur.id, legal_name="Kunde GmbH", organization_type="PROPERTY_MANAGEMENT",
        vat_id="DE987654321",
    )
    identity_service.add_address(
        akteur.id, kunde.id, address_type="BILLING",
        street="Elbchaussee", house_number="5", postal_code="22765", city="Hamburg",
    )
    order = auftrag_service.create_work_order(
        akteur.id, property_id=obj.id, title="Auftrag"
    )
    auftrag_service.set_order_evidence(
        akteur.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        akteur.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            akteur.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(akteur.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        akteur.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id, payment_term_days=30,
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": "100",
                "unit": "Stk", "unit_price": "10.00", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            akteur.id, invoice_id=inv.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    return inv


def _veroeffentlicht(akteur):
    inv = _entwurf(akteur)
    beleg_service.publish_invoice(akteur.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


@pytest.mark.django_db
def test_zugferd_pdf_liefert_hybrid_pdf(admin_client, akteur, fake_storage):
    _firmenprofil(akteur)
    inv = _veroeffentlicht(akteur)

    res = admin_client.get(PDF_URL.format(inv.id))
    assert res.status_code == 200
    assert res["Content-Type"] == "application/pdf"
    assert "attachment" in res["Content-Disposition"]
    assert inv.invoice_number in res["Content-Disposition"]

    pdf = res.content
    assert pdf[:4] == b"%PDF"
    name, xml = get_xml_from_pdf(pdf, check_xsd=True)
    assert name == "factur-x.xml"
    assert b"CrossIndustryInvoice" in xml


@pytest.mark.django_db
def test_zugferd_xml_liefert_das_cii_xml(admin_client, akteur):
    _firmenprofil(akteur)
    inv = _veroeffentlicht(akteur)

    res = admin_client.get(XML_URL.format(inv.id))
    assert res.status_code == 200
    assert res["Content-Type"].startswith("application/xml")
    assert b"CrossIndustryInvoice" in res.content
    assert inv.invoice_number.encode() in res.content


@pytest.mark.django_db
def test_zweiter_abruf_liefert_dieselbe_archivierte_datei(
    admin_client, akteur, fake_storage
):
    """GoBD: eine Ausfertigung. Der zweite Abruf rendert NICHT neu."""
    _firmenprofil(akteur)
    inv = _veroeffentlicht(akteur)

    erste = admin_client.get(PDF_URL.format(inv.id)).content
    assert fake_storage.puts == 1
    zweite = admin_client.get(PDF_URL.format(inv.id)).content
    assert zweite == erste
    assert fake_storage.puts == 1


@pytest.mark.django_db
def test_entwurf_hat_keine_erechnung(admin_client, akteur):
    """Ein Entwurf ist keine Rechnung — 404, nicht ein PDF ohne Belegnummer."""
    _firmenprofil(akteur)
    inv = _entwurf(akteur)
    assert admin_client.get(PDF_URL.format(inv.id)).status_code == 404
    assert admin_client.get(XML_URL.format(inv.id)).status_code == 404


@pytest.mark.django_db
def test_unbekannte_rechnung_404(admin_client):
    assert admin_client.get(PDF_URL.format(uuid.uuid4())).status_code == 404
    assert admin_client.get(XML_URL.format(uuid.uuid4())).status_code == 404


@pytest.mark.django_db
def test_ohne_firmenprofil_422_mit_klarem_grund(admin_client, akteur):
    """Kein Aussteller → kein gültiges EN16931-XML. Ehrlicher 422 mit Hinweis,
    statt eine E-Rechnung auszuliefern, die der Empfänger zurückweist."""
    inv = _veroeffentlicht(akteur)
    for url in (PDF_URL, XML_URL):
        res = admin_client.get(url.format(inv.id))
        assert res.status_code == 422
        assert "Firmenprofil" in res.json()["detail"]


@pytest.mark.django_db
def test_ohne_leserecht_403(client_with_role, akteur, fake_storage):
    """MONTEUR hat kein invoicing/LESEN."""
    _firmenprofil(akteur)
    inv = _veroeffentlicht(akteur)
    c = client_with_role("MONTEUR")
    assert c.get(PDF_URL.format(inv.id)).status_code == 403
    assert c.get(XML_URL.format(inv.id)).status_code == 403


@pytest.mark.django_db
def test_ohne_anmeldung_401(anonymous_client, akteur):
    _firmenprofil(akteur)
    inv = _veroeffentlicht(akteur)
    assert anonymous_client.get(PDF_URL.format(inv.id)).status_code == 401
    assert anonymous_client.get(XML_URL.format(inv.id)).status_code == 401
