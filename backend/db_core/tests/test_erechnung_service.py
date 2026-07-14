"""Tests für die E-Rechnung (ZUGFeRD/Factur-X, services/erechnung.py).

Kernpunkte:
- Das CII-XML ist gegen die offizielle EN16931-XSD valide (das prüft factur-x
  beim Erzeugen; ein Test hält das ausdrücklich fest).
- Pflichtfelder (Belegnummer, Datum, Verkäufer, Käufer, Summen) stehen drin.
- Die Steueraufteilung (BG-23) stimmt CENT-GENAU mit den Kopfsummen überein.
- UNTDID-1001- und Rec-20-Mapping inkl. Fallback für unbekannte Einheiten.
- **Snapshot-Härtung**: eine Änderung am Firmenprofil NACH der Veröffentlichung
  ändert das XML nicht.
- Das XML steckt wirklich im Hybrid-PDF (Extraktion über factur-x).
"""
import io
from datetime import date
from decimal import Decimal

import pytest
from facturx import get_xml_from_pdf, xml_check_xsd
from lxml import etree
from pypdf import PdfReader

from db_core import storage as storage_module
from db_core.betriebszeit import betriebs_datum
from db_core.db_context import business_transaction
from db_core.models import PartyAddress
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
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}


class FakeStorage:
    """Objektspeicher im Speicher (wie in test_beleg_pdf_archiv)."""

    def __init__(self):
        self.objects = {}
        self.puts = 0

    def put_object(self, key, data, content_type="application/octet-stream"):
        from hashlib import sha256
        self.puts += 1
        self.objects[key] = bytes(data)
        return storage_module.ObjectInfo(
            storage_key=key, sha256=sha256(data).hexdigest(), size_bytes=len(data)
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
        bic="BYLADEM1001",
        bank_name="Musterbank",
    )
    return profile


def _kunde(app_user):
    """Ein Organisationskunde mit Rechnungsanschrift und USt-IdNr."""
    org = identity_service.create_organization(
        app_user.id,
        legal_name="Hausverwaltung Nord GmbH",
        organization_type="PROPERTY_MANAGEMENT",
        vat_id="DE987654321",
    )
    identity_service.add_address(
        app_user.id, org.id, address_type="BILLING",
        street="Elbchaussee", house_number="5",
        postal_code="22765", city="Hamburg",
        valid_from=date(2020, 1, 1),
    )
    return org


def _published_invoice(app_user, *, lines=None, kunde=None, **invoice_kwargs):
    obj = property_service.create_property(
        app_user.id, name="Wohnanlage Nord", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    kunde = kunde or _kunde(app_user)
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
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        invoice_date=invoice_kwargs.pop("invoice_date", date(2026, 7, 1)),
        lines=lines or [
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
        **invoice_kwargs,
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


def _geladen(invoice_id):
    return beleg_pdf.load_invoice_for_render(invoice_id)


def _xml(app_user, **kwargs):
    inv = _published_invoice(app_user, **kwargs)
    return inv, erechnung_service.build_cii_xml(_geladen(inv.id))


def _wert(baum, pfad):
    knoten = baum.xpath(pfad, namespaces=NS)
    return knoten[0].text if knoten else None


# --- reine Mapping-Funktionen (ohne DB) ------------------------------------

def test_untdid_1001_mapping():
    """Rechnungsarten → UNTDID 1001.

    Gutschrift UND Storno sind 384 („corrected invoice"): unsere Kreditbelege
    tragen negative Beträge, und 381 („credit note") erwartet POSITIVE — der
    Empfänger negierte sonst ein zweites Mal und buchte die Gutschrift als
    Forderung."""
    assert erechnung_service.belegart_code("RECHNUNG") == "380"
    assert erechnung_service.belegart_code("ABSCHLAGSRECHNUNG") == "380"
    assert erechnung_service.belegart_code("TEILRECHNUNG") == "380"
    assert erechnung_service.belegart_code("SCHLUSSRECHNUNG") == "380"
    assert erechnung_service.belegart_code("GUTSCHRIFT") == "384"
    assert erechnung_service.belegart_code("STORNO") == "384"


def test_einheiten_mapping_und_fallback():
    """Freitext-Einheit → UN/CEFACT Rec. 20; Unbekanntes fällt auf C62 zurück,
    statt die E-Rechnung scheitern zu lassen."""
    assert erechnung_service.einheit_code("Stk") == "H87"
    assert erechnung_service.einheit_code("stk.") == "H87"
    assert erechnung_service.einheit_code("Std") == "HUR"
    assert erechnung_service.einheit_code("m") == "MTR"
    assert erechnung_service.einheit_code("m²") == "MTK"
    assert erechnung_service.einheit_code("qm") == "MTK"
    assert erechnung_service.einheit_code("kg") == "KGM"
    assert erechnung_service.einheit_code("l") == "LTR"
    # Unbekannt / leer / „pauschal" → Auffangcode, kein Fehler.
    assert erechnung_service.einheit_code("Rolle") == "C62"
    assert erechnung_service.einheit_code(None) == "C62"
    assert erechnung_service.einheit_code("psch") == "C62"


# --- XML: Validität und Pflichtfelder --------------------------------------

@pytest.mark.django_db
def test_xml_ist_xsd_valide(app_user, firmenprofil):
    _inv, xml = _xml(app_user)
    # factur-x prüft beim Erzeugen bereits; hier NOCH EINMAL ausdrücklich gegen
    # die offizielle XSD — der Test soll die Zusicherung selbst tragen.
    assert xml_check_xsd(xml, flavor="factur-x", level="en16931") is True


@pytest.mark.django_db
def test_xml_traegt_die_pflichtfelder(app_user, firmenprofil):
    inv, xml = _xml(app_user)
    baum = etree.fromstring(xml)

    assert _wert(baum, "//rsm:ExchangedDocument/ram:ID") == inv.invoice_number
    assert _wert(baum, "//rsm:ExchangedDocument/ram:TypeCode") == "380"
    assert _wert(
        baum, "//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"
    ) == "20260701"
    assert _wert(baum, "//ram:InvoiceCurrencyCode") == "EUR"

    # Verkäufer inkl. USt-IdNr. und Anschrift (aus dem Firmenprofil)
    assert _wert(baum, "//ram:SellerTradeParty/ram:Name") == "Mitra Sanitär GmbH"
    assert _wert(
        baum, "//ram:SellerTradeParty/ram:PostalTradeAddress/ram:CityName"
    ) == "Musterstadt"
    steuern = baum.xpath(
        "//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID",
        namespaces=NS,
    )
    ids = {(n.get("schemeID"), n.text) for n in steuern}
    assert ("VA", "DE123456789") in ids       # BT-31 USt-IdNr.
    assert ("FC", "12/345/67890") in ids      # BT-32 Steuernummer

    # Käufer mit Anschrift und USt-IdNr.
    assert _wert(baum, "//ram:BuyerTradeParty/ram:Name") == "Hausverwaltung Nord GmbH"
    assert _wert(
        baum, "//ram:BuyerTradeParty/ram:PostalTradeAddress/ram:PostcodeCode"
    ) == "22765"
    assert _wert(
        baum, "//ram:BuyerTradeParty/ram:SpecifiedTaxRegistration/ram:ID"
    ) == "DE987654321"

    # Bankverbindung (BT-84/86)
    assert _wert(baum, "//ram:PayeePartyCreditorFinancialAccount/ram:IBANID") == (
        "DE02120300000000202051"
    )
    assert _wert(
        baum, "//ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID"
    ) == "BYLADEM1001"

    # Leistungsort = Liegenschaft (ShipToTradeParty)
    assert _wert(baum, "//ram:ShipToTradeParty/ram:Name") == "Wohnanlage Nord"

    # Summen
    summen = "//ram:SpecifiedTradeSettlementHeaderMonetarySummation"
    assert _wert(baum, f"{summen}/ram:LineTotalAmount") == "240.00"
    assert _wert(baum, f"{summen}/ram:TaxBasisTotalAmount") == "240.00"
    assert _wert(baum, f"{summen}/ram:TaxTotalAmount") == "45.60"
    assert _wert(baum, f"{summen}/ram:GrandTotalAmount") == "285.60"
    assert _wert(baum, f"{summen}/ram:DuePayableAmount") == "285.60"


@pytest.mark.django_db
def test_steuergruppen_stimmen_cent_genau_mit_den_kopfsummen(app_user, firmenprofil):
    """Zwei Steuersätze → zwei BG-23-Gruppen, deren Summen exakt die Kopfsummen
    ergeben. Ein Cent Abweichung wäre beim Empfänger ein Buchungsfehler."""
    inv, xml = _xml(
        app_user,
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 3,
             "unit": "Stk", "unit_price": "33.33", "tax_code": "DE_19"},
            {"line_type": "MATERIAL", "description": "Broschüre", "quantity": 7,
             "unit": "Stk", "unit_price": "1.11", "tax_code": "DE_7"},
        ],
    )
    baum = etree.fromstring(xml)
    # BG-23 steht im KOPF-Settlement; die Positionen tragen ihre eigene
    # ApplicableTradeTax — der Pfad muss beides auseinanderhalten.
    gruppen = baum.xpath(
        "//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax", namespaces=NS
    )
    assert len(gruppen) == 2

    basis = Decimal(0)
    steuer = Decimal(0)
    for g in gruppen:
        b = Decimal(g.xpath("ram:BasisAmount", namespaces=NS)[0].text)
        s = Decimal(g.xpath("ram:CalculatedAmount", namespaces=NS)[0].text)
        satz = Decimal(g.xpath("ram:RateApplicablePercent", namespaces=NS)[0].text)
        assert g.xpath("ram:CategoryCode", namespaces=NS)[0].text == "S"
        # Die Steuer je Gruppe ist auf dieser Gruppe gerundet — nicht positionsweise.
        assert s == (b * satz / Decimal(100)).quantize(Decimal("0.01"))
        basis += b
        steuer += s

    assert basis == inv.net_total
    assert steuer == inv.tax_total
    assert basis + steuer == inv.gross_total


@pytest.mark.django_db
def test_alternativ_und_bedarfspositionen_stehen_nicht_im_xml(app_user, firmenprofil):
    """Nicht summenwirksame Positionen wurden nie berechnet — im XML hätten sie
    nichts verloren (der Empfänger läse Beträge, die keine Forderung sind)."""
    _inv, xml = _xml(
        app_user,
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
             "unit": "Stk", "unit_price": "10.00", "tax_code": "DE_19"},
            {"line_type": "MATERIAL", "description": "Edelziegel (Alternative)",
             "quantity": 10, "unit": "Stk", "unit_price": "50.00",
             "tax_code": "DE_19", "line_kind": "ALTERNATIV"},
            {"line_type": "MATERIAL", "description": "Reserve (Bedarf)",
             "quantity": 5, "unit": "Stk", "unit_price": "20.00",
             "tax_code": "DE_19", "line_kind": "BEDARF"},
            {"line_type": "TEXT", "description": "Hinweis ohne Betrag"},
        ],
    )
    baum = etree.fromstring(xml)
    zeilen = baum.xpath("//ram:IncludedSupplyChainTradeLineItem", namespaces=NS)
    assert len(zeilen) == 1
    namen = zeilen[0].xpath(".//ram:SpecifiedTradeProduct/ram:Name", namespaces=NS)
    assert namen[0].text == "Ziegel"
    assert "Alternative" not in xml.decode()


@pytest.mark.django_db
def test_positionsrabatt_wird_als_nachlass_abgebildet(app_user, firmenprofil):
    """Ein Positionsrabatt steht als Positions-Nachlass (BG-27) im XML — der
    Einzelpreis bleibt derselbe wie im PDF, statt in einen Mischpreis zu wandern."""
    inv, xml = _xml(
        app_user,
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
             "unit": "Stk", "unit_price": "10.00", "discount_percent": "10",
             "tax_code": "DE_19"},
        ],
    )
    assert inv.net_total == Decimal("90.00")
    baum = etree.fromstring(xml)
    assert _wert(baum, "//ram:NetPriceProductTradePrice/ram:ChargeAmount") == "10.00"
    assert _wert(baum, "//ram:SpecifiedTradeSettlementLineMonetarySummation"
                       "/ram:LineTotalAmount") == "90.00"
    nachlass = baum.xpath(
        "//ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeAllowanceCharge",
        namespaces=NS,
    )
    assert len(nachlass) == 1
    assert nachlass[0].xpath("ram:ActualAmount", namespaces=NS)[0].text == "10.00"
    assert nachlass[0].xpath("ram:BasisAmount", namespaces=NS)[0].text == "100.00"


@pytest.mark.django_db
def test_skonto_steht_als_zahlungsbedingung_im_xml(app_user, firmenprofil):
    """BT-20 trägt genau den Text, den der Kunde im PDF liest (Skonto-Slice)."""
    inv, xml = _xml(
        app_user, payment_term_days=30, discount_percent="2", discount_days=10
    )
    baum = etree.fromstring(xml)
    bedingung = _wert(
        baum, "//ram:SpecifiedTradePaymentTerms/ram:Description"
    )
    # Der ABSCHLIESSENDE Zeilenumbruch ist Pflicht: BR-DE-18 verlangt hinter dem
    # letzten #…#-Block einen Umbruch. Ohne ihn verwirft der Referenz-Validator
    # die Konventionszeile — deshalb steht er hier ausdrücklich in der Zusicherung
    # (gegengeprüft in test_erechnung_konformitaet.py).
    assert bedingung.endswith("#\n")
    klartext, maschine, rest = bedingung.split("\n")
    assert rest == ""
    # Erste Zeile: wörtlich derselbe Text wie im PDF (der Mensch liest ihn).
    assert klartext == beleg_pdf.zahlungsbedingungen_text(inv)
    assert "Skonto" in klartext
    # Zweite Zeile: die ZUGFeRD-Konvention (die Software liest sie). Ohne sie wäre
    # der Skonto-Slice maschinell wirkungslos — EN16931 hat kein Skonto-Feld.
    assert maschine == "#SKONTO#TAGE=10#PROZENT=2.00#BASISBETRAG=285.60#"
    assert inv.gross_total == Decimal("285.60")
    # BT-9 Fälligkeit
    assert _wert(
        baum,
        "//ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString",
    ) == "20260731"


@pytest.mark.django_db
def test_ohne_skonto_bleibt_bt20_reiner_klartext(app_user, firmenprofil):
    inv, xml = _xml(app_user, payment_term_days=14)
    baum = etree.fromstring(xml)
    bedingung = _wert(baum, "//ram:SpecifiedTradePaymentTerms/ram:Description")
    assert "#SKONTO#" not in bedingung
    assert bedingung == beleg_pdf.zahlungsbedingungen_text(inv)


def _kreditbeleg_pruefen(ursprung, kredit):
    """Gemeinsame Zusicherungen für Storno und Gutschrift."""
    xml = erechnung_service.build_cii_xml(_geladen(kredit.id))
    assert xml_check_xsd(xml, flavor="factur-x", level="en16931") is True

    baum = etree.fromstring(xml)
    # 384 (corrected invoice), NICHT 381: unsere Beträge sind negativ, und 381
    # („credit note") erwartet positive — der Empfänger negierte doppelt.
    assert _wert(baum, "//rsm:ExchangedDocument/ram:TypeCode") == "384"
    preis = Decimal(_wert(baum, "//ram:NetPriceProductTradePrice/ram:ChargeAmount"))
    menge = Decimal(_wert(baum, "//ram:BilledQuantity"))
    betrag = Decimal(
        _wert(baum, "//ram:SpecifiedTradeSettlementLineMonetarySummation"
                    "/ram:LineTotalAmount")
    )
    # BR-27: kein negativer Einzelpreis. Vorzeichen liegt auf der Menge.
    assert preis > 0
    assert menge < 0
    erste = sorted(kredit.lines.all(), key=lambda l: l.position_number)[0]
    assert betrag == erste.net_amount < 0
    summen = "//ram:SpecifiedTradeSettlementHeaderMonetarySummation"
    assert Decimal(_wert(baum, f"{summen}/ram:GrandTotalAmount")) == kredit.gross_total
    # Bezug auf den Ursprungsbeleg (BG-3)
    assert _wert(
        baum, "//ram:InvoiceReferencedDocument/ram:IssuerAssignedID"
    ) == ursprung.invoice_number
    return baum


@pytest.mark.django_db
def test_storno_traegt_384_und_nichtnegative_preise(app_user, firmenprofil):
    inv = _published_invoice(app_user)
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    _kreditbeleg_pruefen(inv, storno)


@pytest.mark.django_db
def test_gutschrift_traegt_384_und_nichtnegative_preise(app_user, firmenprofil):
    """Die Teilkorrektur (GUTSCHRIFT) folgt derselben Konvention wie das
    Vollstorno — sonst läse der Empfänger zwei verschiedene Regeln."""
    inv = _published_invoice(app_user)
    gs = beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[1])
    _kreditbeleg_pruefen(inv, gs)


@pytest.mark.django_db
def test_kreditbeleg_mit_positionsrabatt(app_user, firmenprofil):
    """Kreditbeleg MIT Rabatt: Basis und Nachlass sind negativ, die Rechnung geht
    trotzdem auf (BT-131 = Menge × Preis − Nachlass) und das XML bleibt valide."""
    inv = _published_invoice(
        app_user,
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
             "unit": "Stk", "unit_price": "10.00", "discount_percent": "10",
             "tax_code": "DE_19"},
        ],
    )
    assert inv.net_total == Decimal("90.00")
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    assert storno.net_total == Decimal("-90.00")
    baum = _kreditbeleg_pruefen(inv, storno)

    nachlass = baum.xpath(
        "//ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeAllowanceCharge",
        namespaces=NS,
    )
    assert len(nachlass) == 1
    betrag = Decimal(nachlass[0].xpath("ram:ActualAmount", namespaces=NS)[0].text)
    basis = Decimal(nachlass[0].xpath("ram:BasisAmount", namespaces=NS)[0].text)
    menge = Decimal(_wert(baum, "//ram:BilledQuantity"))
    preis = Decimal(_wert(baum, "//ram:NetPriceProductTradePrice/ram:ChargeAmount"))
    zeilenbetrag = Decimal(
        _wert(baum, "//ram:SpecifiedTradeSettlementLineMonetarySummation"
                    "/ram:LineTotalAmount")
    )
    assert basis == Decimal("-100.00")
    assert betrag == Decimal("-10.00")
    assert zeilenbetrag == menge * preis - betrag == Decimal("-90.00")


@pytest.mark.django_db
def test_pdf_und_xml_zeigen_dieselbe_menge(app_user, firmenprofil):
    """ZUGFeRD verlangt, dass Sichtbild und Daten denselben Inhalt tragen. Bei
    einem Kreditbeleg liegt das Vorzeichen in BEIDEN auf der Menge."""
    inv = _published_invoice(app_user)   # 100 Stk x 2,40
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    pdf = erechnung_service.render_zugferd_pdf(_geladen(storno.id))
    text = " ".join(
        " ".join(s.extract_text().split()) for s in PdfReader(io.BytesIO(pdf)).pages
    )
    _name, xml = get_xml_from_pdf(pdf, check_xsd=True)
    baum = etree.fromstring(xml)
    menge = Decimal(_wert(baum, "//ram:BilledQuantity"))
    preis = Decimal(_wert(baum, "//ram:NetPriceProductTradePrice/ram:ChargeAmount"))
    assert (menge, preis) == (Decimal("-100.000"), Decimal("2.40"))

    # Genau das steht auch im Sichtbild: negative Menge, POSITIVER Einzelpreis.
    assert "-100" in text
    assert "2,40 EUR" in text
    assert "-2,40 EUR" not in text
    assert "-240,00 EUR" in text  # Betrag/Summen bleiben negativ


@pytest.mark.django_db
def test_reverse_charge_traegt_kategorie_ae_mit_begruendung(app_user, firmenprofil):
    """§13b-Bauleistung → Kategorie AE mit Befreiungsgrund (BT-120); ohne den
    weist ein EN16931-Prüfer die Rechnung zurück."""
    _inv, xml = _xml(
        app_user,
        lines=[
            {"line_type": "FREMDLEISTUNG", "description": "Bauleistung",
             "quantity": 1, "unit": "psch", "unit_price": "1000.00",
             "tax_code": "DE_13B"},
        ],
    )
    baum = etree.fromstring(xml)
    tax = baum.xpath(
        "//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax", namespaces=NS
    )[0]
    assert tax.xpath("ram:CategoryCode", namespaces=NS)[0].text == "AE"
    assert tax.xpath("ram:RateApplicablePercent", namespaces=NS)[0].text == "0.00"
    grund = tax.xpath("ram:ExemptionReason", namespaces=NS)
    assert grund and "13b" in grund[0].text
    assert _wert(baum, "//ram:BilledQuantity") is not None


# --- Snapshot-Härtung ------------------------------------------------------

@pytest.mark.django_db
def test_firmenprofil_aenderung_nach_veroeffentlichung_aendert_das_xml_nicht(
    app_user, firmenprofil
):
    """Der Kern der Snapshot-Härtung: der Beleg ist eingefroren. Wer danach
    umzieht, ändert keinen gestellten Beleg."""
    inv, vorher = _xml(app_user)
    assert b"Industriestra" in vorher

    firma_service.update_company_profile(
        app_user.id,
        company_name="Mitra Sanitär & Heizung GmbH",
        street="Neue Allee 99",
        postal_code="99999",
        city="Neustadt",
        vat_id="DE999999999",
        iban="DE02120300000000202051",
    )
    nachher = erechnung_service.build_cii_xml(_geladen(inv.id))
    assert nachher == vorher
    baum = etree.fromstring(nachher)
    assert _wert(baum, "//ram:SellerTradeParty/ram:Name") == "Mitra Sanitär GmbH"
    assert _wert(
        baum, "//ram:SellerTradeParty/ram:PostalTradeAddress/ram:CityName"
    ) == "Musterstadt"


@pytest.mark.django_db
def test_kunden_umzug_nach_veroeffentlichung_aendert_das_xml_nicht(
    app_user, firmenprofil
):
    kunde = _kunde(app_user)
    inv = _published_invoice(app_user, kunde=kunde)
    vorher = erechnung_service.build_cii_xml(_geladen(inv.id))

    # Echter Umzug: die bisherige Rechnungsadresse beenden (die Exclusion
    # excl_party_address_primary lässt keine zwei zeitgleich primären zu), dann
    # die neue eintragen.
    #
    # `betriebs_datum()` und NICHT `date.today()`: Letzteres liest die
    # OS-Zeitzone (hier Europe/Berlin), `party_address` rechnet aber im
    # Betriebsdatum. Auf einem Rechner in einer anderen Zeitzone — oder zwischen
    # 00:00 und 02:00 MESZ — liefen die beiden auseinander und der Test schlug
    # aus einem Grund fehl, der mit der E-Rechnung nichts zu tun hat.
    heute = betriebs_datum()
    with business_transaction(app_user.id):
        PartyAddress.objects.filter(
            party_id=kunde.id, address_type="BILLING"
        ).update(valid_until=heute)
    identity_service.add_address(
        app_user.id, kunde.id, address_type="BILLING",
        street="Ganz Woanders", house_number="1",
        postal_code="80331", city="München",
        valid_from=heute,
    )
    assert beleg_service.party_address(kunde.id).postal_code == "80331"

    nachher = erechnung_service.build_cii_xml(_geladen(inv.id))
    assert nachher == vorher
    assert b"22765" in nachher
    assert b"80331" not in nachher


@pytest.mark.django_db
def test_snapshot_enthaelt_die_stammdaten(app_user, firmenprofil):
    inv = _published_invoice(app_user)
    header = inv.billing_snapshot["header"]
    assert header["snapshot_version"] == beleg_service.SNAPSHOT_VERSION
    assert header["issuer"]["company_name"] == "Mitra Sanitär GmbH"
    assert header["issuer"]["vat_id"] == "DE123456789"
    assert header["delivery"]["name"] == "Wohnanlage Nord"
    partei = inv.billing_snapshot["parties"][0]
    assert partei["snapshot"]["display_name"] == "Hausverwaltung Nord GmbH"
    assert partei["snapshot"]["address"]["postal_code"] == "22765"
    assert partei["snapshot"]["vat_id"] == "DE987654321"


@pytest.mark.django_db
def test_ohne_firmenprofil_gibt_es_keine_erechnung(app_user):
    """Ohne Aussteller-Stammdaten kein gültiges EN16931-XML — ehrlich abbrechen
    (422 in der API) statt eine E-Rechnung zu erzeugen, die zurückgewiesen wird."""
    inv = _published_invoice(app_user)
    with pytest.raises(erechnung_service.ERechnungError, match="Firmenprofil"):
        erechnung_service.build_cii_xml(_geladen(inv.id))
    # Die Meldung darf NICHT zum „Neu ausstellen" auffordern — ein veröffentlichter
    # Beleg ist unveränderlich.
    with pytest.raises(erechnung_service.ERechnungError) as exc:
        erechnung_service.build_cii_xml(_geladen(inv.id))
    assert "neu ausstellen" not in str(exc.value)


@pytest.mark.django_db
def test_spaeter_gepflegtes_firmenprofil_repariert_die_erechnung(app_user):
    """Beleg veröffentlicht, BEVOR das Firmenprofil gepflegt war: der Snapshot
    trägt `issuer: null`. Das ist eine LÜCKE, keine eingefrorene Aussage — sobald
    das Profil da ist, zieht der Fallback es je Feld nach. Ohne das bliebe der
    Beleg für immer ausstellerlos (der Beleg lässt sich nicht neu ausstellen)."""
    inv = _published_invoice(app_user)
    assert (
        inv.billing_snapshot["header"]["snapshot_version"]
        == beleg_service.SNAPSHOT_VERSION
    )
    assert inv.billing_snapshot["header"]["issuer"] is None

    firma_service.update_company_profile(
        app_user.id, company_name="Spät Gepflegt GmbH",
        street="Nachzügler 1", postal_code="10115", city="Berlin",
        vat_id="DE111111111",
    )
    geladen = _geladen(inv.id)
    stamm = beleg_service.beleg_stammdaten(geladen)
    assert stamm["aus_snapshot"] is False
    assert stamm["issuer"]["company_name"] == "Spät Gepflegt GmbH"
    # Der eingefrorene Snapshot bleibt unangetastet (B-30).
    geladen.refresh_from_db()
    assert geladen.billing_snapshot["header"]["issuer"] is None

    xml = erechnung_service.build_cii_xml(_geladen(inv.id))
    assert xml_check_xsd(xml, flavor="factur-x", level="en16931") is True
    baum = etree.fromstring(xml)
    assert _wert(baum, "//ram:SellerTradeParty/ram:Name") == "Spät Gepflegt GmbH"
    # Der Kunde stand im Snapshot und kommt weiterhin von dort.
    assert _wert(baum, "//ram:BuyerTradeParty/ram:Name") == "Hausverwaltung Nord GmbH"


# --- Hybrid-PDF ------------------------------------------------------------

@pytest.mark.django_db
def test_hybrid_pdf_traegt_das_xml_und_ist_pdfa3(app_user, firmenprofil):
    inv = _published_invoice(app_user)
    pdf = erechnung_service.render_zugferd_pdf(_geladen(inv.id))
    assert pdf[:4] == b"%PDF"

    # Das XML steckt wirklich drin (Extraktion durch factur-x selbst).
    name, xml = get_xml_from_pdf(pdf, check_xsd=True)
    assert name == "factur-x.xml"
    assert xml == erechnung_service.build_cii_xml(_geladen(inv.id))

    # PDF/A-3B-Marker: OutputIntent (fpdf2) + unkomprimiertes XMP mit pdfaid.
    assert b"OutputIntent" in pdf
    assert b"<pdfaid:part>3</pdfaid:part>" in pdf
    assert b"<pdfaid:conformance>B</pdfaid:conformance>" in pdf
    # Der Anhang ist als Alternative-Darstellung desselben Belegs deklariert.
    assert b"/Alternative" in pdf

    # Das Sichtbild ist das Beleg-Layout (dieselbe Quelle wie das normale PDF).
    text = " ".join(
        " ".join(s.extract_text().split()) for s in PdfReader(io.BytesIO(pdf)).pages
    )
    assert inv.invoice_number in text
    assert "Mitra Sanitär GmbH" in text
    assert "Hausverwaltung Nord GmbH" in text


@pytest.mark.django_db
def test_hybrid_pdf_mit_firmenlogo(app_user, firmenprofil, fake_storage):
    """Ein PNG mit Alphakanal wird von fpdf2 als Bild + SMask eingebettet.
    PDF/A-3B (anders als PDF/A-1) erlaubt Transparenz — der PDF/A-Modus muss das
    Logo also annehmen, statt zu scheitern, und die Marker müssen stehen bleiben."""
    from db_core.tests.test_beleg_pdf import PNG_1x1

    inv = _published_invoice(app_user)
    firma_service.set_company_logo(app_user.id, dateiname="logo.png", inhalt=PNG_1x1)

    pdf = erechnung_service.render_zugferd_pdf(_geladen(inv.id))
    assert pdf[:4] == b"%PDF"
    assert b"/Subtype /Image" in pdf          # Logo ist drin
    assert b"OutputIntent" in pdf             # PDF/A-Marker unverändert
    assert b"<pdfaid:part>3</pdfaid:part>" in pdf
    name, xml = get_xml_from_pdf(pdf, check_xsd=True)
    assert name == "factur-x.xml"
    assert xml_check_xsd(xml, flavor="factur-x", level="en16931") is True


@pytest.mark.django_db
def test_entwurf_und_unbekannt_haben_keine_erechnung(app_user, firmenprofil):
    import uuid

    obj = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="Weg 1", postal_code="10115", city="Berlin",
    )
    entwurf = beleg_service.create_invoice(
        app_user.id, property_id=obj.id,
        lines=[{"line_type": "MATERIAL", "description": "X", "quantity": 1,
                "unit": "Stk", "unit_price": "1.00", "tax_code": "DE_19"}],
    )
    assert erechnung_service.build_cii_xml_for(entwurf.id) is None
    assert erechnung_service.build_cii_xml_for(uuid.uuid4()) is None
    assert erechnung_service.get_or_archive_zugferd_pdf(
        app_user.id, entwurf.id
    ) is None


# --- Archivierung ----------------------------------------------------------

@pytest.mark.django_db
def test_archivierung_liefert_beim_zweiten_abruf_dieselbe_datei(
    app_user, firmenprofil, fake_storage
):
    """Erster Abruf rendert + archiviert; jeder weitere liefert exakt dieselben
    Bytes aus dem Objektspeicher (GoBD: eine Ausfertigung)."""
    inv = _published_invoice(app_user)
    erste = erechnung_service.get_or_archive_zugferd_pdf(app_user.id, inv.id)
    assert erste[:4] == b"%PDF"
    assert fake_storage.puts == 1

    zweite = erechnung_service.get_or_archive_zugferd_pdf(app_user.id, inv.id)
    assert zweite == erste
    assert fake_storage.puts == 1  # nicht neu abgelegt

    key = erechnung_service._archived_key(inv.id)
    assert key is not None and key.startswith("belege/erechnung/")
    assert fake_storage.objects[key] == erste


@pytest.mark.django_db
def test_erechnung_und_beleg_pdf_sind_zwei_ausfertigungen(
    app_user, firmenprofil, fake_storage
):
    """Beide Kategorien nebeneinander — der partielle UNIQUE-Index (0032/0059)
    greift je Kategorie, nicht gegeneinander."""
    inv = _published_invoice(app_user)
    beleg = beleg_pdf.get_or_archive_invoice_pdf(app_user.id, inv.id)
    erechnung = erechnung_service.get_or_archive_zugferd_pdf(app_user.id, inv.id)
    assert beleg != erechnung
    assert fake_storage.puts == 2
    assert beleg_pdf.archived_key_for("invoice_id", inv.id) is not None
    assert erechnung_service._archived_key(inv.id) is not None


@pytest.mark.django_db
def test_archivierung_degradiert_ohne_objektspeicher(app_user, firmenprofil, monkeypatch):
    """Kaputter Objektspeicher darf den Beleg nicht unzugänglich machen."""
    inv = _published_invoice(app_user)

    def kaputt():
        raise storage_module.StorageError("MinIO weg")

    monkeypatch.setattr(storage_module, "get_storage", kaputt)
    pdf = erechnung_service.get_or_archive_zugferd_pdf(app_user.id, inv.id)
    assert pdf is not None and pdf[:4] == b"%PDF"
    assert erechnung_service._archived_key(inv.id) is None  # nichts registriert


@pytest.mark.django_db
def test_inkonsistente_kopfsummen_verweigern_die_erechnung(app_user, firmenprofil):
    """Driften Kopfsummen und Positionen auseinander (Altdaten), wird KEIN XML
    ausgeliefert — der Empfänger bucht sonst falsche Beträge."""
    inv = _published_invoice(app_user)
    # Am Trigger vorbei ist die Rechnung eingefroren; hier reicht ein
    # In-Memory-Objekt, um den Prüfpfad zu treffen.
    geladen = _geladen(inv.id)
    geladen.tax_total = Decimal("99.99")
    with pytest.raises(erechnung_service.ERechnungError, match="Kopfsummen"):
        erechnung_service.build_cii_xml(geladen)


@pytest.mark.django_db
def test_altbeleg_ohne_snapshot_stammdaten_faellt_auf_livedaten_zurueck(
    app_user, firmenprofil
):
    """Belege, die VOR der Snapshot-Härtung veröffentlicht wurden, tragen keine
    eingefrorenen Stammdaten. Sie bleiben ausstellbar (Live-Fallback) — der
    Snapshot wird NICHT nachträglich umgeschrieben (B-30)."""
    inv = _published_invoice(app_user)
    # Den Snapshot auf den alten Aufbau zurückstutzen — NUR im Speicher: die
    # veröffentlichte Rechnung ist per DB-Trigger unveränderlich (B-21), und genau
    # das ist der Grund für den Live-Fallback.
    geladen = _geladen(inv.id)
    alt = dict(geladen.billing_snapshot)
    alt["header"] = {
        k: v for k, v in alt["header"].items()
        if k not in ("snapshot_version", "issuer", "delivery")
    }
    alt["parties"] = [
        {k: v for k, v in p.items() if k != "snapshot"} for p in alt["parties"]
    ]
    geladen.billing_snapshot = alt
    stamm = beleg_service.beleg_stammdaten(geladen)
    assert stamm["aus_snapshot"] is False
    xml = erechnung_service.build_cii_xml(geladen)
    assert xml_check_xsd(xml, flavor="factur-x", level="en16931") is True
    baum = etree.fromstring(xml)
    assert _wert(baum, "//ram:SellerTradeParty/ram:Name") == "Mitra Sanitär GmbH"
    assert _wert(baum, "//ram:BuyerTradeParty/ram:Name") == "Hausverwaltung Nord GmbH"
