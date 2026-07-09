"""Service-Tests der Buchhaltungs-Schicht (Zahlungen + Mahnwesen) gegen die echte
Test-DB.

Die DB-Tore sind scharf: Zahlung nur auf veröffentlichte Rechnung (B-23);
payment ist append-only; Mahnung nur auf veröffentlichte, fällige Rechnung mit
lückenlos aufsteigender Stufe (B-22). Der Publish-Pfad vergibt Nummer/Snapshot
per BEFORE-Trigger; das DEFERRED Beteiligten-Tor feuert unter der pytest-
Transaktion nicht, stört die Zahlungs-/Mahn-Trigger (sofort) aber nicht.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from db_core.models import Payment
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _property(app_user):
    return property_service.create_property(
        app_user.id, name="Buchhaltungs-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user, first="Wanda", last="WEG"):
    return identity_service.create_person(app_user.id, first_name=first, last_name=last)


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


def _published(app_user, *, due_days):
    """Veröffentlichte Rechnung mit due_date relativ zu heute (negativ = fällig)."""
    obj = _property(app_user)
    weg = _party(app_user)
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        invoice_date=date.today() - timedelta(days=90),
        due_date=date.today() + timedelta(days=due_days),
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


def _entwurf(app_user):
    obj = _property(app_user)
    return beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 1,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )


# --- Zahlungen -------------------------------------------------------------

@pytest.mark.django_db
def test_record_payment_auf_veroeffentlichte(app_user):
    inv = _published(app_user, due_days=-10)
    p = buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("100.00"),
        paid_at=date.today(), payment_type="TEILZAHLUNG",
    )
    assert p.payment_type == "TEILZAHLUNG"
    assert p.amount == Decimal("100.00")


@pytest.mark.django_db
def test_record_payment_auf_entwurf_scheitert(app_user):
    """B-23: Zahlung nur auf veröffentlichte Rechnung."""
    inv = _entwurf(app_user)
    with pytest.raises(ValueError):
        buchhaltung_service.record_payment(
            app_user.id, invoice_id=inv.id, amount=Decimal("50.00"),
            paid_at=date.today(),
        )


@pytest.mark.django_db
def test_record_payment_betrag_null(app_user):
    inv = _published(app_user, due_days=-10)
    with pytest.raises(ValueError):
        buchhaltung_service.record_payment(
            app_user.id, invoice_id=inv.id, amount=Decimal("0"), paid_at=date.today()
        )


@pytest.mark.django_db
def test_record_payment_ungueltiger_typ(app_user):
    inv = _published(app_user, due_days=-10)
    with pytest.raises(ValueError):
        buchhaltung_service.record_payment(
            app_user.id, invoice_id=inv.id, amount=Decimal("10"),
            paid_at=date.today(), payment_type="SPENDE",
        )


# --- Zahlung stornieren (Gegenbuchung) -------------------------------------

@pytest.mark.django_db
def test_reverse_payment_erzeugt_gegenbuchung(app_user):
    inv = _published(app_user, due_days=-10)
    p = buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("100.00"), paid_at=date.today()
    )
    storno = buchhaltung_service.reverse_payment(app_user.id, payment_id=p.id)
    assert storno.payment_type == "STORNO_BUCHUNG"
    assert storno.amount == Decimal("100.00")
    assert Payment.objects.filter(invoice_id=inv.id).count() == 2


@pytest.mark.django_db
def test_reverse_payment_nicht_doppelt(app_user):
    inv = _published(app_user, due_days=-10)
    p = buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("100.00"), paid_at=date.today()
    )
    buchhaltung_service.reverse_payment(app_user.id, payment_id=p.id)
    with pytest.raises(ValueError):
        buchhaltung_service.reverse_payment(app_user.id, payment_id=p.id)


@pytest.mark.django_db
def test_reverse_storno_verboten(app_user):
    inv = _published(app_user, due_days=-10)
    p = buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("100.00"), paid_at=date.today()
    )
    storno = buchhaltung_service.reverse_payment(app_user.id, payment_id=p.id)
    with pytest.raises(ValueError):
        buchhaltung_service.reverse_payment(app_user.id, payment_id=storno.id)


@pytest.mark.django_db
def test_reverse_rueckerstattung_verboten(app_user):
    """Eine bereits negativ gewertete Buchung (RUECKERSTATTUNG) darf nicht per
    Gegenbuchung storniert werden — sonst zählte das Vorzeichen doppelt."""
    inv = _published(app_user, due_days=-10)
    r = buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("50.00"),
        paid_at=date.today(), payment_type="RUECKERSTATTUNG",
    )
    with pytest.raises(ValueError):
        buchhaltung_service.reverse_payment(app_user.id, payment_id=r.id)


# --- Mahnwesen -------------------------------------------------------------

@pytest.mark.django_db
def test_issue_dunning_stufe1(app_user):
    inv = _published(app_user, due_days=-30)
    n = buchhaltung_service.issue_dunning_notice(
        app_user.id, invoice_id=inv.id, level=1,
        issued_at=date.today() - timedelta(days=1),
    )
    assert n.level_id == 1


@pytest.mark.django_db
def test_issue_dunning_luecke_verboten(app_user):
    """B-22: Stufen müssen lückenlos aufsteigen — Stufe 2 ohne Stufe 1 scheitert."""
    inv = _published(app_user, due_days=-30)
    with pytest.raises(ValueError):
        buchhaltung_service.issue_dunning_notice(
            app_user.id, invoice_id=inv.id, level=2,
            issued_at=date.today() - timedelta(days=1),
        )


@pytest.mark.django_db
def test_issue_dunning_nicht_faellig_scheitert(app_user):
    """B-22: Mahnung nur auf fällige Rechnung (issued_at > due_date)."""
    inv = _published(app_user, due_days=30)  # in der Zukunft fällig
    with pytest.raises(ValueError):
        buchhaltung_service.issue_dunning_notice(
            app_user.id, invoice_id=inv.id, level=1, issued_at=date.today()
        )


@pytest.mark.django_db
def test_issue_dunning_auf_entwurf_scheitert(app_user):
    inv = _entwurf(app_user)
    with pytest.raises(ValueError):
        buchhaltung_service.issue_dunning_notice(
            app_user.id, invoice_id=inv.id, level=1, issued_at=date.today()
        )
