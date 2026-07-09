"""API-Tests der Buchhaltungs-Endpoints (offene Posten, Zahlungen, Mahnwesen)
über den Django-Test-Client. Read-only; Setup baut über die Services eine
veröffentlichte, fällige Rechnung mit Teilzahlung und Mahnstufe 1.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from db_core.models import AppUser
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

User = get_user_model()


def _logged_in_client(client):
    from .conftest import grant_role
    user = User.objects.create_user(username=f"u{uuid.uuid4().hex[:8]}", password="x")
    au = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Login", status="ACTIVE", version=1
    )
    user.app_user_id = au.id
    user.save()
    grant_role(au.id, "ADMINISTRATION")
    client.force_login(user)
    return client


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
def test_offene_posten_liste(admin_client, seeded):
    r = admin_client.get("/api/buchhaltung/invoices?payment_status=TEILZAHLUNG")
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
def test_bezahlt_nach_restzahlung(admin_client, seeded, app_user):
    """Restzahlung bis zur Bruttosumme → Status BEZAHLT, offener Betrag 0."""
    inv = seeded["inv"]
    remaining = inv.gross_total - Decimal("100.00")
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=remaining, paid_at=date.today()
    )
    body = admin_client.get(f"/api/buchhaltung/invoices/{inv.id}").json()
    assert body["payment_status"] == "BEZAHLT"
    assert Decimal(body["open_amount"]) == Decimal("0.00")


@pytest.mark.django_db
def test_unbekannter_payment_status_422(admin_client, seeded):
    r = admin_client.get("/api/buchhaltung/invoices?payment_status=QUATSCH")
    assert r.status_code == 422


@pytest.mark.django_db
def test_offene_posten_detail(admin_client, seeded):
    r = admin_client.get(f"/api/buchhaltung/invoices/{seeded['inv'].id}")
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
def test_detail_entwurf_404(admin_client, app_user):
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
    r = admin_client.get(f"/api/buchhaltung/invoices/{inv.id}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_detail_404(admin_client, db):
    r = admin_client.get(f"/api/buchhaltung/invoices/{uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_mahnliste(admin_client, seeded):
    r = admin_client.get("/api/buchhaltung/dunning")
    assert r.status_code == 200
    body = r.json()
    assert [lv["level"] for lv in body["levels"]] == [1, 2, 3]
    row = next(i for i in body["items"] if i["id"] == str(seeded["inv"].id))
    assert row["dunning_level"] == 1
    assert row["days_overdue"] >= 30
    assert Decimal(row["open_amount"]) > 0


@pytest.mark.django_db
def test_cancel_eingeloggt_und_referenz(client, seeded):
    inv = seeded["inv"]
    c = _logged_in_client(client)
    r = c.post(f"/api/buchhaltung/invoices/{inv.id}/cancel", content_type="application/json")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["invoice_type"] == "STORNO"
    assert body["invoice_number"].startswith("GS-")
    # Ursprung listet den Stornobeleg als credit_note.
    detail = c.get(f"/api/buchhaltung/invoices/{inv.id}").json()
    assert any(cn["id"] == body["id"] for cn in detail["credit_notes"])
    # Der Stornobeleg verweist zurück auf den Ursprung.
    storno_detail = c.get(f"/api/buchhaltung/invoices/{body['id']}").json()
    assert storno_detail["origin"]["id"] == str(inv.id)


@pytest.mark.django_db
def test_cancel_ohne_login_abgelehnt(anonymous_client, seeded):
    r = anonymous_client.post(f"/api/buchhaltung/invoices/{seeded['inv'].id}/cancel",
                    content_type="application/json")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_correction_eingeloggt(client, seeded):
    c = _logged_in_client(client)
    r = c.post(
        f"/api/buchhaltung/invoices/{seeded['inv'].id}/correction",
        data={"positions": [1]}, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["invoice_type"] == "GUTSCHRIFT"


@pytest.mark.django_db
def test_correction_unbekannte_position_422(client, seeded):
    c = _logged_in_client(client)
    r = c.post(
        f"/api/buchhaltung/invoices/{seeded['inv'].id}/correction",
        data={"positions": [99]}, content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_mahnliste_levelfilter(admin_client, seeded):
    r = admin_client.get("/api/buchhaltung/dunning?level=1")
    ids = {i["id"] for i in r.json()["items"]}
    assert str(seeded["inv"].id) in ids
    r0 = admin_client.get("/api/buchhaltung/dunning?level=0")
    ids0 = {i["id"] for i in r0.json()["items"]}
    assert str(seeded["inv"].id) not in ids0
