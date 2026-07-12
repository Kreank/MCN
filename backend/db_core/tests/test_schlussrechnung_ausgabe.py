"""Ausgabe einer Schlussrechnung mit Anrechnung: E-Rechnung (CII) und PDF.

Die Anrechnung steht als NEGATIVE Position im Beleg. Für die Ausgabe gilt:

- **BR-27** (EN16931) verbietet einen negativen Nettoeinzelpreis → das Vorzeichen
  liegt auf der MENGE (`beleg.anzeige_menge_preis`, dieselbe Funktion für PDF und
  XML). Das XML ist gegen die offizielle XSD valide (das prüft `build_cii_xml`).
- **BT-113 (TotalPrepaidAmount) wird bewusst NICHT benutzt** — er ist der
  *gezahlte* Betrag und mindert nur den Zahlbetrag, nicht die Steuer. Unsere
  Abschlagsrechnung kann unbezahlt sein, und § 14 Abs. 5 UStG verlangt, die
  Teilentgelte samt Steuer abzusetzen. Ein Test hält das ausdrücklich fest.
- Die angerechneten Belege stehen als BG-3 (BT-25/BT-26) im XML.
- Das PDF weist die Anrechnung aus (Leistung, Abschläge mit Nummer/Datum,
  Zahlbetrag).
"""
from datetime import date
from decimal import Decimal

import pytest
from lxml import etree

from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf
from db_core.services import erechnung as erechnung_service
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:"
           "ReusableAggregateBusinessInformationEntity:100",
}


@pytest.fixture
def firmenprofil(app_user):
    profile, _ = firma_service.update_company_profile(
        app_user.id,
        company_name="Mitra Sanitär GmbH",
        street="Industriestraße 5",
        postal_code="12345",
        city="Musterstadt",
        tax_number="12/345/67890",
        vat_id="DE123456789",
        iban="DE02120300000000202051",
    )
    return profile


@pytest.fixture
def schlussrechnung(app_user, firmenprofil):
    """Auftrag → Abschlagsrechnung (1.000 €) → Schlussrechnung (5.000 €)."""
    obj = property_service.create_property(
        app_user.id, name="Wohnanlage Nord", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_organization(
        app_user.id, legal_name="Hausverwaltung Nord GmbH",
        organization_type="PROPERTY_MANAGEMENT", vat_id="DE987654321",
    )
    identity_service.add_address(
        app_user.id, kunde.id, address_type="BILLING", street="Elbchaussee",
        house_number="5", postal_code="22765", city="Hamburg",
        valid_from=date(2020, 1, 1),
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Bad-Sanierung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)

    def _beteiligte(inv):
        for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
            beleg_service.add_invoice_party(
                app_user.id, invoice_id=inv.id, party_id=kunde.id, role=role,
                is_primary=True,
            )

    ar = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="ABSCHLAGSRECHNUNG",
        work_order_id=order.id, invoice_date=date(2026, 6, 1),
        lines=[{"line_type": "PAUSCHALE", "description": "1. Abschlag", "quantity": 1,
                "unit": "psch", "unit_price": "1000.00", "tax_code": "DE_19"}],
    )
    _beteiligte(ar)
    beleg_service.publish_invoice(app_user.id, invoice_id=ar.id)
    ar.refresh_from_db()

    for to in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)

    sr = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="SCHLUSSRECHNUNG",
        work_order_id=order.id, invoice_date=date(2026, 7, 1),
        lines=[{"line_type": "PAUSCHALE", "description": "Gesamtleistung Bad",
                "quantity": 1, "unit": "psch", "unit_price": "5000.00",
                "tax_code": "DE_19"}],
        advance_invoice_ids=[ar.id],
    )
    _beteiligte(sr)
    beleg_service.publish_invoice(app_user.id, invoice_id=sr.id)
    sr.refresh_from_db()
    return {"ar": ar, "sr": sr}


def _baum(invoice):
    xml = erechnung_service.build_cii_xml(beleg_pdf.load_invoice_for_render(invoice.id))
    return etree.fromstring(xml)


def _werte(baum, pfad):
    return [k.text for k in baum.xpath(pfad, namespaces=NS)]


@pytest.mark.django_db
def test_erechnung_der_schlussrechnung_ist_valide_und_stimmt(schlussrechnung):
    """XSD-valide (sonst wirft build_cii_xml) und cent-genau zum Beleg."""
    sr = schlussrechnung["sr"]
    baum = _baum(sr)
    summen = "//ram:SpecifiedTradeSettlementHeaderMonetarySummation"
    assert _werte(baum, f"{summen}/ram:LineTotalAmount") == ["4000.00"]
    assert _werte(baum, f"{summen}/ram:TaxTotalAmount") == ["760.00"]
    assert _werte(baum, f"{summen}/ram:GrandTotalAmount") == ["4760.00"]
    assert _werte(baum, f"{summen}/ram:DuePayableAmount") == ["4760.00"]
    # BT-113 (Vorauszahlung) bleibt leer: die Anrechnung ist kein gezahlter Betrag
    # und muss die STEUER mindern — das leisten nur die negativen Positionen.
    assert _werte(baum, f"{summen}/ram:TotalPrepaidAmount") == []
    # Die Steueraufteilung trägt den geminderten Betrag (nicht die Gesamtleistung).
    assert _werte(baum, "//ram:ApplicableTradeTax/ram:BasisAmount") == ["4000.00"]
    assert _werte(baum, "//ram:ApplicableTradeTax/ram:CalculatedAmount") == ["760.00"]


@pytest.mark.django_db
def test_anrechnungsposition_hat_negative_menge_und_positiven_preis(schlussrechnung):
    """BR-27: kein negativer Einzelpreis — das Vorzeichen liegt auf der Menge."""
    baum = _baum(schlussrechnung["sr"])
    mengen = _werte(baum, "//ram:IncludedSupplyChainTradeLineItem"
                          "/ram:SpecifiedLineTradeDelivery/ram:BilledQuantity")
    preise = _werte(baum, "//ram:IncludedSupplyChainTradeLineItem"
                          "/ram:SpecifiedLineTradeAgreement"
                          "/ram:NetPriceProductTradePrice/ram:ChargeAmount")
    betraege = _werte(baum, "//ram:IncludedSupplyChainTradeLineItem"
                            "/ram:SpecifiedLineTradeSettlement"
                            "/ram:SpecifiedTradeSettlementLineMonetarySummation"
                            "/ram:LineTotalAmount")
    assert mengen == ["1.000", "-1.000"]
    assert preise == ["5000.00", "1000.00"]
    assert betraege == ["5000.00", "-1000.00"]


@pytest.mark.django_db
def test_angerechnete_abschlaege_stehen_als_bg3_im_xml(schlussrechnung):
    ar = schlussrechnung["ar"]
    baum = _baum(schlussrechnung["sr"])
    refs = _werte(baum, "//ram:InvoiceReferencedDocument/ram:IssuerAssignedID")
    assert refs == [ar.invoice_number]


@pytest.mark.django_db
def test_pdf_weist_die_anrechnung_aus(schlussrechnung):
    """Das Sichtbild nennt Leistung, Abschlag (Nummer + Datum) und Zahlbetrag."""
    from pypdf import PdfReader
    import io

    ar, sr = schlussrechnung["ar"], schlussrechnung["sr"]
    pdf = beleg_pdf.render_invoice_pdf(sr.id)
    text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "Anrechnung der Abschlagsrechnungen" in text
    assert ar.invoice_number in text
    assert "01.06.2026" in text                 # Datum der Abschlagsrechnung
    assert "5.950,00 EUR" in text               # Gesamtleistung brutto
    assert "1.190,00 EUR" in text.replace("-1.190,00 EUR", "1.190,00 EUR")
    assert "Verbleibender Zahlbetrag" in text
    assert "4.760,00 EUR" in text               # Zahlbetrag


@pytest.mark.django_db
def test_snapshot_friert_die_anrechnung_ein(schlussrechnung):
    """Der gehashte Snapshot trägt die Anrechnung — sonst wäre der Beleg aus ihm
    nicht rekonstruierbar (B-21/B-30)."""
    sr = schlussrechnung["sr"]
    advances = sr.billing_snapshot["advances"]
    assert len(advances) == 1
    assert advances[0]["invoice_number"] == schlussrechnung["ar"].invoice_number
    assert Decimal(advances[0]["gross_amount"]) == Decimal("1190.00")
    assert advances[0]["steuergruppen"][0]["tax_code"] == "DE_19"
