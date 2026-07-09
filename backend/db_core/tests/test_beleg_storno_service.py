"""Service-Tests für Storno und Rechnungskorrektur (Folgebelege).

Storno/Gutschrift invertieren die Positionen (negativer unit_price → negativer
net_amount) und werden veröffentlicht; die DB-Tore (Referenz veröffentlicht,
Schuldner-Übereinstimmung P3-06, ≥1 Schuldner + primärer Empfänger A-27/A-28)
sind scharf. STORNO/GUTSCHRIFT sind von der Auftrags-Vorbedingung (B-08) befreit.
"""
import re
from decimal import Decimal

import pytest

from db_core.models import Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _gepruefter_auftrag(app_user, obj, debtor):
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
            app_user.id, work_order_id=order.id, party_id=debtor.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    return order


def _published_invoice(app_user):
    obj = property_service.create_property(
        app_user.id, name="Storno-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="Wanda", last_name="WEG")
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
            {"line_type": "ARBEITSZEIT", "description": "Arbeit", "quantity": 10,
             "unit": "h", "unit_price": "58.00", "tax_code": "DE_19"},
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
def test_cancellation_vollstorno(app_user):
    origin = _published_invoice(app_user)
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=origin.id)
    assert storno.invoice_type == "STORNO"
    assert storno.status == "VEROEFFENTLICHT"
    assert re.match(r"^GS-[0-9]{4}-[0-9]{6,}$", storno.invoice_number)
    assert storno.reference_invoice_id == origin.id
    # Vollstorno kehrt die Summe exakt um.
    assert storno.gross_total == -origin.gross_total
    assert storno.work_order_id is None


@pytest.mark.django_db
def test_cancellation_nur_veroeffentlicht(app_user):
    obj = property_service.create_property(
        app_user.id, name="X", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Z", "quantity": 1,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    with pytest.raises(ValueError):
        beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)


@pytest.mark.django_db
def test_cancellation_summen_vollstaendig_negiert(app_user):
    origin = _published_invoice(app_user)
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=origin.id)
    assert storno.net_total == -origin.net_total
    assert storno.tax_total == -origin.tax_total
    assert storno.gross_total == -origin.gross_total


@pytest.mark.django_db
def test_storno_of_storno_verboten(app_user):
    origin = _published_invoice(app_user)
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=origin.id)
    with pytest.raises(ValueError):
        beleg_service.create_cancellation(app_user.id, invoice_id=storno.id)


@pytest.mark.django_db
def test_doppelstorno_verboten(app_user):
    origin = _published_invoice(app_user)
    beleg_service.create_cancellation(app_user.id, invoice_id=origin.id)
    with pytest.raises(ValueError):
        beleg_service.create_cancellation(app_user.id, invoice_id=origin.id)


@pytest.mark.django_db
def test_correction_teilmenge(app_user):
    origin = _published_invoice(app_user)
    # Nur Position 1 (Material 100×2,40 = 240 netto) korrigieren.
    gutschrift = beleg_service.create_correction(
        app_user.id, invoice_id=origin.id, positions=[1]
    )
    assert gutschrift.invoice_type == "GUTSCHRIFT"
    assert gutschrift.status == "VEROEFFENTLICHT"
    assert gutschrift.net_total == Decimal("-240.00")
    assert re.match(r"^GS-[0-9]{4}-[0-9]{6,}$", gutschrift.invoice_number)


@pytest.mark.django_db
def test_correction_unbekannte_position(app_user):
    origin = _published_invoice(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_correction(app_user.id, invoice_id=origin.id, positions=[99])


@pytest.mark.django_db
def test_correction_ohne_position(app_user):
    origin = _published_invoice(app_user)
    with pytest.raises(ValueError):
        beleg_service.create_correction(app_user.id, invoice_id=origin.id, positions=[])


@pytest.mark.django_db
def test_storno_zeilen_sind_negiert(app_user):
    origin = _published_invoice(app_user)
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=origin.id)
    lines = list(
        Invoice.objects.get(id=storno.id).lines.order_by("position_number")
    )
    assert len(lines) == 2
    for line in lines:
        assert line.quantity > 0  # DB-CHECK quantity > 0 bleibt gewahrt
        assert line.unit_price < 0  # Invertierung über den Preis
        assert line.net_amount < 0