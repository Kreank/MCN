"""Service-Tests der Live-Vorschau (Editor rechnet kein Geld, Server schon).

Die Vorschau nimmt denselben Payload wie das PUT und führt DIESELBE Rechnung aus,
ohne zu persistieren. Der harte Nachweis ist deshalb die Deckungsgleichheit:
Vorschau-Summen == Summen nach echtem PUT für denselben Payload — inklusive des
Schlussrechnungs-Sonderfalls, in dem die Anrechnung der Abschläge in die Summen
einfließt, obwohl sie nicht im Editor-Payload steht.
"""
import uuid
from decimal import Decimal

import pytest

from db_core.models import InvoiceAdvance, QuoteLine
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _property(app_user):
    return property_service.create_property(
        app_user.id, name="Vorschau-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _leeres_angebot(app_user):
    obj = _property(app_user)
    return beleg_service.create_quote(app_user.id, property_id=obj.id, title="Leer")


# --- (a) Vorschau == echtes PUT (Angebot) ----------------------------------

@pytest.mark.django_db
def test_vorschau_quote_summen_wie_put(app_user):
    quote = _leeres_angebot(app_user)
    lines = [
        {"line_type": "MATERIAL", "description": "Rinne", "quantity": 2,
         "unit_price": 50, "tax_code": "DE_19", "unit_cost": "30.00"},
        {"line_type": "ARBEITSZEIT", "description": "Montage", "quantity": 3,
         "unit_price": 60, "tax_code": "DE_19"},
        {"line_type": "TEXT", "description": "Hinweis"},
    ]
    v = beleg_service.vorschau_quote(
        quote.id, lines=lines, rubriken=[], mit_kalkulation=True
    )
    beleg_service.update_quote(app_user.id, quote_id=quote.id, lines=lines, rubriken=[])
    quote.refresh_from_db()

    assert v["net_total"] == quote.net_total
    assert v["tax_total"] == quote.tax_total
    assert v["gross_total"] == quote.gross_total
    # Zeilen in Payload-Reihenfolge; Positionsnetto und abgeleiteter Aufschlag.
    assert v["lines"][0]["net_amount"] == Decimal("100.00")
    assert v["lines"][0]["markup_percent"] == Decimal("66.667")  # (50-30)/30
    assert v["lines"][1]["net_amount"] == Decimal("180.00")
    # Textzeile trägt keinen Betrag.
    assert v["lines"][2]["net_amount"] is None


# --- (b) Text-/Alternativ-/Bedarfspositionen zählen nicht in die Summe ------

@pytest.mark.django_db
def test_vorschau_quote_alternativ_und_bedarf_nicht_summenwirksam(app_user):
    quote = _leeres_angebot(app_user)
    lines = [
        {"line_type": "MATERIAL", "description": "Basis", "quantity": 1,
         "unit_price": 100, "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Kupfer statt Zink",
         "quantity": 1, "unit_price": 500, "tax_code": "DE_19",
         "line_kind": "ALTERNATIV"},
        {"line_type": "MATERIAL", "description": "Optional", "quantity": 1,
         "unit_price": 200, "tax_code": "DE_19", "line_kind": "BEDARF"},
        {"line_type": "TEXT", "description": "Zwischentext"},
    ]
    v = beleg_service.vorschau_quote(
        quote.id, lines=lines, rubriken=[], mit_kalkulation=True
    )
    # Nur die NORMAL-Position zählt.
    assert v["net_total"] == Decimal("100.00")
    # Die Alternativ-/Bedarfszeilen tragen ihren Betrag (fürs UI), die Textzeile null.
    assert v["lines"][1]["net_amount"] == Decimal("500.00")
    assert v["lines"][2]["net_amount"] == Decimal("200.00")
    assert v["lines"][3]["net_amount"] is None
    gesamt = v["kalkulation"]["gesamt"]
    assert gesamt["netto"] == Decimal("100.00")
    assert gesamt["alternativ_netto"] == Decimal("500.00")
    assert gesamt["bedarf_netto"] == Decimal("200.00")


# --- (c) Ungültige Position → 422 (ValueError), Beleg fehlt → 404 -----------

@pytest.mark.django_db
def test_vorschau_quote_ungueltige_position_wirft_valueerror(app_user):
    quote = _leeres_angebot(app_user)
    with pytest.raises(ValueError):
        # Betragszeile ohne tax_code — dieselbe Meldung wie beim PUT.
        beleg_service.vorschau_quote(
            quote.id,
            lines=[{"line_type": "MATERIAL", "description": "Ohne Steuer",
                    "quantity": 1, "unit_price": 10}],
            rubriken=[], mit_kalkulation=True,
        )


@pytest.mark.django_db
def test_vorschau_quote_unbekannt_wirft_nichtgefunden(app_user):
    with pytest.raises(beleg_service.BelegNichtGefunden):
        beleg_service.vorschau_quote(
            uuid.uuid4(), lines=[], rubriken=[], mit_kalkulation=True
        )


# --- (d) Kalkulation nur mit pricing-Recht (Service-Flag) -------------------

@pytest.mark.django_db
def test_vorschau_quote_kalkulation_flag(app_user):
    quote = _leeres_angebot(app_user)
    lines = [{"line_type": "MATERIAL", "description": "A", "quantity": 1,
              "unit_price": 100, "tax_code": "DE_19", "unit_cost": "60.00"}]

    ohne = beleg_service.vorschau_quote(
        quote.id, lines=lines, rubriken=[], mit_kalkulation=False
    )
    assert ohne["kalkulation"] is None
    # Die (preisfreien) Summen kommen trotzdem.
    assert ohne["net_total"] == Decimal("100.00")

    mit = beleg_service.vorschau_quote(
        quote.id, lines=lines, rubriken=[], mit_kalkulation=True
    )
    assert mit["kalkulation"] is not None
    assert mit["kalkulation"]["gesamt"]["netto"] == Decimal("100.00")
    assert mit["kalkulation"]["gesamt"]["ek"] == Decimal("60.00")


# --- (e) Expliziter markup_percent gewinnt und wird gespeichert -------------

@pytest.mark.django_db
def test_expliziter_markup_wird_gespeichert_nicht_abgeleitet(app_user):
    obj = _property(app_user)
    # Abgeleitet wäre (100-40)/40*100 = 150,000 — der explizite Wert 25 muss gewinnen.
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Aufschlag",
        lines=[{"line_type": "MATERIAL", "description": "A", "quantity": 1,
                "unit_price": 100, "tax_code": "DE_19", "unit_cost": "40.00",
                "markup_percent": "25.000"}],
    )
    line = QuoteLine.objects.get(quote_id=quote.id, position_number=1)
    assert line.markup_percent == Decimal("25.000")
    assert line.unit_cost == Decimal("40.00")


# --- Rechnung: schlichter Fall + Schlussrechnung mit Anrechnung -------------

@pytest.mark.django_db
def test_vorschau_invoice_summen_wie_put(app_user):
    obj = _property(app_user)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Alt", "quantity": 1,
                "unit_price": 10, "tax_code": "DE_19"}],
    )
    lines = [
        {"line_type": "MATERIAL", "description": "Neu A", "quantity": 2,
         "unit_price": 50, "tax_code": "DE_19"},
        {"line_type": "ARBEITSZEIT", "description": "Montage", "quantity": 3,
         "unit_price": 60, "tax_code": "DE_19"},
    ]
    v = beleg_service.vorschau_invoice(
        inv.id, lines=lines, rubriken=[], mit_kalkulation=True
    )
    beleg_service.update_invoice(app_user.id, invoice_id=inv.id, lines=lines)
    inv.refresh_from_db()
    assert v["net_total"] == inv.net_total == Decimal("280.00")
    assert v["tax_total"] == inv.tax_total
    assert v["gross_total"] == inv.gross_total


@pytest.mark.django_db
def test_vorschau_invoice_unbekannt_wirft_nichtgefunden(app_user):
    with pytest.raises(beleg_service.BelegNichtGefunden):
        beleg_service.vorschau_invoice(
            uuid.uuid4(), lines=[], rubriken=[], mit_kalkulation=True
        )


# Schlussrechnungs-Setup (verkürzt aus test_schlussrechnung_service.py): ein bis
# KAUFMAENNISCH_GEPRUEFT hochgefahrener Auftrag, zwei veröffentlichte Abschläge,
# dann die Schlussrechnung mit Anrechnung.

def _auftrag_gepruefen(app_user, obj, debtor):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Bad-Sanierung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Auftrag per Mail"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=debtor.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status=to
        )
    order.refresh_from_db()
    return order


def _abschlag(app_user, obj, kunde, order, *, betrag, typ="ABSCHLAGSRECHNUNG"):
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type=typ, work_order_id=order.id,
        lines=[{"line_type": "PAUSCHALE", "description": "Abschlag",
                "quantity": 1, "unit_price": betrag, "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


@pytest.mark.django_db
def test_vorschau_schlussrechnung_rechnet_abschlaege_wie_gespeichert_an(app_user):
    obj = _property(app_user)
    kunde = identity_service.create_person(
        app_user.id, first_name="Sieglinde", last_name="Schuldner"
    )
    order = _auftrag_gepruefen(app_user, obj, kunde)
    ar1 = _abschlag(app_user, obj, kunde, order, betrag="1000.00")
    ar2 = _abschlag(app_user, obj, kunde, order, betrag="1500.00", typ="TEILRECHNUNG")

    leistung = [{"line_type": "PAUSCHALE", "description": "Gesamtleistung",
                 "quantity": 1, "unit_price": "5000.00", "tax_code": "DE_19"}]
    sr = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="SCHLUSSRECHNUNG",
        work_order_id=order.id, lines=leistung,
        advance_invoice_ids=[ar1.id, ar2.id],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=sr.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    sr.refresh_from_db()
    # Verkettung liegt vor: die Anrechnung ist gespeichert (2.500 Rest).
    assert sr.net_total == Decimal("2500.00")
    assert InvoiceAdvance.objects.filter(final_invoice_id=sr.id).count() == 2

    # Die Vorschau bekommt NUR die Leistungszeile (wie der Editor) — die Anrechnung
    # muss sie selbst aus der Verkettung ziehen, sonst stünde 5.000 statt 2.500.
    v = beleg_service.vorschau_invoice(
        sr.id, lines=leistung, rubriken=[], mit_kalkulation=True
    )
    assert v["net_total"] == sr.net_total == Decimal("2500.00")
    assert v["tax_total"] == sr.tax_total == Decimal("475.00")
    assert v["gross_total"] == sr.gross_total == Decimal("2975.00")
    # Die Payload-Antwort bleibt 1:1 zum Payload (nur die Leistungszeile).
    assert len(v["lines"]) == 1
    assert v["lines"][0]["net_amount"] == Decimal("5000.00")
    # Die Anrechnung schlägt auch in der Kalkulation durch (5.000 − 2.500).
    assert v["kalkulation"]["gesamt"]["netto"] == Decimal("2500.00")

    # Und deckungsgleich mit dem echten PUT desselben Payloads.
    beleg_service.update_invoice(app_user.id, invoice_id=sr.id, lines=leistung)
    sr.refresh_from_db()
    assert v["net_total"] == sr.net_total
    assert v["gross_total"] == sr.gross_total
