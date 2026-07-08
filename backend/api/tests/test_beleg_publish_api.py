"""API-Tests für Veröffentlichung (Rechnung) und Versand (Angebot)."""
import uuid

import pytest
from django.contrib.auth import get_user_model

from db_core.models import AppUser
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

User = get_user_model()


def _logged_in_client(client):
    user = User.objects.create_user(username=f"u{uuid.uuid4().hex[:8]}", password="x")
    au = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Login", status="ACTIVE", version=1
    )
    user.app_user_id = au.id
    user.save()
    client.force_login(user)
    return client


def _gepruefter_auftrag(app_user, obj, debtor):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag"
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


@pytest.mark.django_db
def test_publish_invoice_ueber_api(client, app_user):
    obj = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    weg = identity_service.create_person(app_user.id, first_name="W", last_name="EG")
    order = _gepruefter_auftrag(app_user, obj, weg)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )
    c = _logged_in_client(client)
    assert c.post(
        f"/api/invoicing/invoices/{inv.id}/parties",
        data={"party_id": str(weg.id), "role": "INVOICE_DEBTOR", "is_primary": True},
        content_type="application/json",
    ).status_code == 201
    assert c.post(
        f"/api/invoicing/invoices/{inv.id}/parties",
        data={"party_id": str(weg.id), "role": "INVOICE_RECIPIENT", "is_primary": True},
        content_type="application/json",
    ).status_code == 201

    r = c.post(f"/api/invoicing/invoices/{inv.id}/publish", content_type="application/json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "VEROEFFENTLICHT"
    assert body["invoice_number"].startswith("RE-")
    assert body["has_snapshot"] is True
    assert body["work_order_number"].startswith("AU-")
    assert len(body["parties"]) == 2


@pytest.mark.django_db
def test_publish_ohne_login_abgelehnt(client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[
            {"line_type": "MATERIAL", "description": "X", "quantity": 1,
             "unit_price": "1.00", "tax_code": "DE_19"},
        ],
    )
    r = client.post(f"/api/invoicing/invoices/{inv.id}/publish", content_type="application/json")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_publish_ohne_auftrag_422(client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[
            {"line_type": "MATERIAL", "description": "X", "quantity": 1,
             "unit_price": "1.00", "tax_code": "DE_19"},
        ],
    )
    c = _logged_in_client(client)
    r = c.post(f"/api/invoicing/invoices/{inv.id}/publish", content_type="application/json")
    assert r.status_code == 422


@pytest.mark.django_db
def test_send_quote_ueber_api(client, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="WEG", street="W",
        postal_code="1", city="Berlin",
    )
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 5,
             "unit_price": "2.40", "tax_code": "DE_19"},
        ],
    )
    c = _logged_in_client(client)
    r = c.post(f"/api/invoicing/quotes/{q.id}/send", content_type="application/json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "VERSENDET"
    assert body["quote_number"].startswith("AN-")
    assert body["has_snapshot"] is True
