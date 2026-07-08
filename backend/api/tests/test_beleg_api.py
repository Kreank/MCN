"""API-Tests der Beleg-Endpoints (Angebote) über den Django-Test-Client."""
import uuid

import pytest

from django.contrib.auth import get_user_model

from db_core.models import AppUser, Quote
from db_core.services import beleg as beleg_service
from db_core.services import property as property_service

User = get_user_model()


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Angebotsobjekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Dachreparatur",
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
             "unit_price": 3, "tax_code": "DE_19"},
            {"line_type": "ARBEITSZEIT", "description": "Arbeit", "quantity": 2,
             "unit_price": 45, "tax_code": "DE_19"},
        ],
    )
    return {"app_user": app_user, "obj": obj, "quote": q}


def _logged_in_client(client, *, with_app_user=True):
    user = User.objects.create_user(username=f"u{uuid.uuid4().hex[:8]}", password="x")
    if with_app_user:
        au = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Login", status="ACTIVE", version=1
        )
        user.app_user_id = au.id
        user.save()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_liste(client, seeded):
    r = client.get("/api/invoicing/quotes")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["title"] == "Dachreparatur"
    assert item["status"] == "ENTWURF"
    assert item["quote_number"] is None
    assert item["property"]["city"] == "Berlin"


@pytest.mark.django_db
def test_liste_property_filter(client, seeded):
    r = client.get(f"/api/invoicing/quotes?property_id={seeded['obj'].id}")
    assert r.json()["total"] == 1
    r2 = client.get(f"/api/invoicing/quotes?property_id={uuid.uuid4()}")
    assert r2.json()["total"] == 0


@pytest.mark.django_db
def test_detail_mit_positionen(client, seeded):
    r = client.get(f"/api/invoicing/quotes/{seeded['quote'].id}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["lines"]) == 2
    assert body["lines"][0]["position_number"] == 1
    assert body["lines"][0]["description"] == "Ziegel"
    # 10*3=30, 2*45=90 -> net 120
    assert body["net_total"] == "120.00"


@pytest.mark.django_db
def test_detail_404(client, seeded):
    r = client.get(f"/api/invoicing/quotes/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_create_eingeloggt(client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/quotes",
        data={
            "property_id": str(obj.id),
            "title": "Neues Angebot",
            "lines": [
                {"line_type": "MATERIAL", "description": "Pos", "quantity": 1,
                 "unit_price": 100, "tax_code": "DE_19"}
            ],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["title"] == "Neues Angebot"
    assert body["status"] == "ENTWURF"
    assert body["net_total"] == "100.00"
    assert Quote.objects.filter(id=body["id"]).exists()


@pytest.mark.django_db
def test_create_ungueltige_position_422(client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/quotes",
        data={
            "property_id": str(obj.id), "title": "X",
            "lines": [{"line_type": "MATERIAL", "description": "kein Preis"}],
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_ohne_login_abgelehnt(client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    r = client.post(
        "/api/invoicing/quotes",
        data={"property_id": str(obj.id), "title": "Anon", "lines": []},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_rechnung_liste_und_detail(client, app_user):
    obj = property_service.create_property(
        app_user.id, name="RE-Objekt", property_type="WEG",
        street="W", house_number="2", postal_code="10115", city="Berlin",
    )
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Pos", "quantity": 2,
                "unit_price": 50, "tax_code": "DE_19"}],
    )
    r = client.get("/api/invoicing/invoices")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["invoice_type"] == "RECHNUNG"
    assert r.json()["items"][0]["invoice_number"] is None

    d = client.get(f"/api/invoicing/invoices/{inv.id}")
    assert d.status_code == 200
    body = d.json()
    assert len(body["lines"]) == 1
    assert body["net_total"] == "100.00"


@pytest.mark.django_db
def test_rechnung_detail_404(client, db):
    r = client.get(f"/api/invoicing/invoices/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_rechnung_create_eingeloggt(client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/invoicing/invoices",
        data={
            "property_id": str(obj.id), "invoice_type": "RECHNUNG",
            "lines": [{"line_type": "MATERIAL", "description": "P", "quantity": 1,
                       "unit_price": 100, "tax_code": "DE_19"}],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["gross_total"] == "119.00"


@pytest.mark.django_db
def test_rechnung_create_ohne_login(client, db, app_user):
    obj = property_service.create_property(
        app_user.id, name="O", property_type="OTHER",
        street="S", postal_code="1", city="C",
    )
    r = client.post(
        "/api/invoicing/invoices",
        data={"property_id": str(obj.id), "invoice_type": "RECHNUNG", "lines": []},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)
