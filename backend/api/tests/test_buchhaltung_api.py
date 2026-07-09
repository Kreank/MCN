"""API-Tests der Buchhaltungs-Endpoints (offene Posten, Zahlungen, Mahnwesen)
über den Django-Test-Client. Read-only; Setup baut über die Services eine
veröffentlichte, fällige Rechnung mit Teilzahlung und Mahnstufe 1.
"""
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
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


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="OP-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    weg = identity_service.create_person(
        app_user.id, first_name="Petra", last_name="Prinzipal"
    )
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        invoice_date=date.today() - timedelta(days=90),
        due_date=date.today() - timedelta(days=30),
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
    # Teilzahlung (Netto 240 + 19% = 285,60 brutto → 100 offen bleibt Teilzahlung).
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("100.00"),
        paid_at=date.today() - timedelta(days=5), payment_type="TEILZAHLUNG",
    )
    buchhaltung_service.issue_dunning_notice(
        app_user.id, invoice_id=inv.id, level=1,
        issued_at=date.today() - timedelta(days=1), note="Erste Erinnerung",
    )
    return {"inv": inv, "weg": weg}


@pytest.mark.django_db
def test_offene_posten_liste(client, seeded):
    r = client.get("/api/buchhaltung/invoices?payment_status=TEILZAHLUNG")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    it = body["items"][0]
    assert it["payment_status"] == "TEILZAHLUNG"
    assert Decimal(it["paid_total"]) == Decimal("100.00")
    assert Decimal(it["open_amount"]) == Decimal(it["gross_total"]) - Decimal("100.00")
    assert it["is_overdue"] is True
    assert it["debtor"] == "Petra Prinzipal"
    assert it["dunning_level"] == 1


@pytest.mark.django_db
def test_bezahlt_nach_restzahlung(client, seeded, app_user):
    """Restzahlung bis zur Bruttosumme → Status BEZAHLT, offener Betrag 0."""
    inv = seeded["inv"]
    remaining = inv.gross_total - Decimal("100.00")
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=remaining, paid_at=date.today()
    )
    body = client.get(f"/api/buchhaltung/invoices/{inv.id}").json()
    assert body["payment_status"] == "BEZAHLT"
    assert Decimal(body["open_amount"]) == Decimal("0.00")


@pytest.mark.django_db
def test_unbekannter_payment_status_422(client, seeded):
    r = client.get("/api/buchhaltung/invoices?payment_status=QUATSCH")
    assert r.status_code == 422


@pytest.mark.django_db
def test_offene_posten_detail(client, seeded):
    r = client.get(f"/api/buchhaltung/invoices/{seeded['inv'].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["invoice_number"].startswith("RE-")
    assert [p["payment_type"] for p in body["payments"]] == ["TEILZAHLUNG"]
    assert body["dunning"][0]["level"] == 1
    assert body["dunning"][0]["label"] == "Zahlungserinnerung"
    assert body["reference"]["project_name"] is None or isinstance(
        body["reference"]["project_name"], str
    )
    assert body["reference"]["work_order_number"].startswith("AU-")


@pytest.mark.django_db
def test_detail_entwurf_404(client, app_user):
    """Nicht veröffentlichte Rechnung ist kein offener Posten."""
    obj = property_service.create_property(
        app_user.id, name="X", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Z", "quantity": 1,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    r = client.get(f"/api/buchhaltung/invoices/{inv.id}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_detail_404(client, db):
    r = client.get(f"/api/buchhaltung/invoices/{uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_mahnliste(client, seeded):
    r = client.get("/api/buchhaltung/dunning")
    assert r.status_code == 200
    body = r.json()
    assert [lv["level"] for lv in body["levels"]] == [1, 2, 3]
    row = next(i for i in body["items"] if i["id"] == str(seeded["inv"].id))
    assert row["dunning_level"] == 1
    assert row["days_overdue"] >= 30
    assert Decimal(row["open_amount"]) > 0


@pytest.mark.django_db
def test_mahnliste_levelfilter(client, seeded):
    r = client.get("/api/buchhaltung/dunning?level=1")
    ids = {i["id"] for i in r.json()["items"]}
    assert str(seeded["inv"].id) in ids
    r0 = client.get("/api/buchhaltung/dunning?level=0")
    ids0 = {i["id"] for i in r0.json()["items"]}
    assert str(seeded["inv"].id) not in ids0
