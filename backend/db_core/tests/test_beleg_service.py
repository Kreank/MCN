"""Service-Tests der Beleg-Schicht (Angebote) gegen die echte Test-DB.

Der DB-CHECK erzwingt net_amount = round(quantity*unit_price*(1-discount/100),2);
ein falsch berechneter Betrag ließe den INSERT scheitern — die Tests prüfen
also implizit die Rundung. tax_code-Startwerte (DE_19 etc.) kommen aus 0016.
"""
from decimal import Decimal

import pytest

from db_core.models import InvoiceLine, QuoteLine
from db_core.services import beleg as beleg_service
from db_core.services import property as property_service


def _property(app_user):
    return property_service.create_property(
        app_user.id, name="Beleg-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


@pytest.mark.django_db
def test_create_quote_entwurf(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot Dachrinne",
        lines=[
            {"line_type": "MATERIAL", "description": "Dachrinne verzinkt",
             "quantity": 2, "unit": "m", "unit_price": 10, "tax_code": "DE_19"},
        ],
    )
    assert q.status == "ENTWURF"
    assert q.quote_number is None  # Nummer erst bei Versand
    assert q.net_total == Decimal("20.00")
    assert q.tax_total == Decimal("3.80")
    assert q.gross_total == Decimal("23.80")
    line = QuoteLine.objects.get(quote_id=q.id, position_number=1)
    assert line.net_amount == Decimal("20.00")
    assert line.tax_rate_percent == Decimal("19.00")


@pytest.mark.django_db
def test_create_quote_mit_rabatt_und_text(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot mit Rabatt",
        lines=[
            {"line_type": "TEXT", "description": "Leistungsbeschreibung folgt"},
            {"line_type": "ARBEITSZEIT", "description": "Montage",
             "quantity": 3, "unit_price": 50, "discount_percent": 10, "tax_code": "DE_19"},
        ],
    )
    # 3 * 50 * 0.9 = 135.00
    line = QuoteLine.objects.get(quote_id=q.id, position_number=2)
    assert line.net_amount == Decimal("135.00")
    assert q.net_total == Decimal("135.00")
    # Textzeile trägt keine Beträge
    text = QuoteLine.objects.get(quote_id=q.id, position_number=1)
    assert text.net_amount is None


@pytest.mark.django_db
def test_create_quote_leerer_titel(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_quote(app_user.id, property_id=obj.id, title="  ")


@pytest.mark.django_db
def test_create_quote_ungueltiger_line_type(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            lines=[{"line_type": "FALSCH", "description": "y"}],
        )


@pytest.mark.django_db
def test_create_quote_betragszeile_ohne_tax_code(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            lines=[{"line_type": "MATERIAL", "description": "y",
                    "quantity": 1, "unit_price": 5}],
        )


@pytest.mark.django_db
def test_create_quote_betragszeile_ohne_menge(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            lines=[{"line_type": "MATERIAL", "description": "y",
                    "unit_price": 5, "tax_code": "DE_19"}],
        )


@pytest.mark.django_db
def test_create_quote_menge_null_oder_negativ(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            lines=[{"line_type": "MATERIAL", "description": "y", "quantity": 0,
                    "unit_price": 5, "tax_code": "DE_19"}],
        )


@pytest.mark.django_db
def test_create_quote_rabatt_ausserhalb_bereich(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            lines=[{"line_type": "MATERIAL", "description": "y", "quantity": 1,
                    "unit_price": 5, "discount_percent": 150, "tax_code": "DE_19"}],
        )


@pytest.mark.django_db
def test_create_quote_ueberzaehlige_nachkommastellen(app_user):
    """W1: quantity mit >3 Nachkommastellen wird auf die Spaltenskala
    quantisiert; net_amount passt danach exakt zum DB-CHECK (kein 500)."""
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Präzision",
        lines=[{"line_type": "MATERIAL", "description": "x", "quantity": "1.5555",
                "unit_price": 100, "tax_code": "DE_19"}],
    )
    line = QuoteLine.objects.get(quote_id=q.id, position_number=1)
    assert line.quantity == Decimal("1.556")  # auf 3 Stellen gerundet
    assert line.net_amount == Decimal("155.60")  # round(1.556*100, 2)


@pytest.mark.django_db
def test_create_quote_steuer_pro_gruppe_gerundet(app_user):
    """W2: Kopf-Steuer wird pro Steuergruppe gerundet (wie assert_quote_totals),
    nicht pro Zeile. Zwei Zeilen à netto 0.03 @19% -> round(0.06*0.19,2)=0.01."""
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Rundung",
        lines=[
            {"line_type": "MATERIAL", "description": "a", "quantity": "0.03",
             "unit_price": 1, "tax_code": "DE_19"},
            {"line_type": "MATERIAL", "description": "b", "quantity": "0.03",
             "unit_price": 1, "tax_code": "DE_19"},
        ],
    )
    assert q.net_total == Decimal("0.06")
    assert q.tax_total == Decimal("0.01")  # nicht 0.02 (Pro-Zeile-Rundung)


@pytest.mark.django_db
def test_create_invoice_entwurf(app_user):
    obj = _property(app_user)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[
            {"line_type": "MATERIAL", "description": "Dachziegel",
             "quantity": 10, "unit_price": 3, "tax_code": "DE_19"},
        ],
    )
    assert inv.status == "ENTWURF"
    assert inv.invoice_number is None  # Nummer erst bei Veröffentlichung
    assert inv.invoice_type == "RECHNUNG"
    assert inv.net_total == Decimal("30.00")
    assert inv.gross_total == Decimal("35.70")
    assert InvoiceLine.objects.filter(invoice_id=inv.id).count() == 1


@pytest.mark.django_db
def test_create_invoice_ungueltiger_typ(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_invoice(
            app_user.id, property_id=obj.id, invoice_type="FALSCH",
        )


@pytest.mark.django_db
def test_create_gutschrift_via_create_invoice_verboten(app_user):
    """Gutschrift/Storno entstehen nur über create_cancellation/create_correction,
    nicht direkt über create_invoice (sonst „positive Gutschrift"-Inkonsistenz)."""
    obj = _property(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_invoice(
            app_user.id, property_id=obj.id, invoice_type="GUTSCHRIFT",
        )
