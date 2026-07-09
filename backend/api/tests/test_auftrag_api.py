"""API-Tests der Auftrags-Endpoints über den Django-Test-Client."""
import uuid

import pytest
from django.contrib.auth import get_user_model

from db_core.models import AppUser, WorkOrder
from db_core.services import auftrag as auftrag_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

User = get_user_model()


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Auftragshaus", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    a1 = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Heizung erneuern"
    )
    a2 = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Fassade streichen"
    )
    return {"app_user": app_user, "obj": obj, "a1": a1, "a2": a2}


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


@pytest.mark.django_db
def test_liste_und_pagination(admin_client, seeded):
    r = admin_client.get("/api/workflow/work_orders?page=1&page_size=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


@pytest.mark.django_db
def test_suche_und_projektfilter(admin_client, seeded):
    r = admin_client.get("/api/workflow/work_orders?q=Fassade")
    titles = {i["title"] for i in r.json()["items"]}
    assert titles == {"Fassade streichen"}
    r2 = admin_client.get(f"/api/workflow/work_orders?property_id={seeded['obj'].id}")
    assert r2.json()["total"] == 2


@pytest.mark.django_db
def test_detail_mit_verlauf(admin_client, seeded):
    r = admin_client.get(f"/api/workflow/work_orders/{seeded['a1'].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Heizung erneuern"
    assert body["status"] == "ENTWURF"
    assert body["order_number"].startswith("AU-")
    # Der Initial-Status ENTWURF wird per Trigger protokolliert.
    assert body["history"][-1]["to_status"] == "ENTWURF"


@pytest.mark.django_db
def test_detail_404(admin_client, db):
    r = admin_client.get(f"/api/workflow/work_orders/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_create_eingeloggt(client, app_user):
    obj = property_service.create_property(
        app_user.id, name="Neubau", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    c = _logged_in_client(client)
    r = c.post(
        "/api/workflow/work_orders",
        data={"property_id": str(obj.id), "title": "Dach neu"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["title"] == "Dach neu"
    assert body["order_number"].startswith("AU-")
    assert body["status"] == "ENTWURF"
    assert WorkOrder.objects.filter(id=body["id"]).exists()


@pytest.mark.django_db
def test_create_ohne_login_abgelehnt(anonymous_client, seeded):
    r = anonymous_client.post(
        "/api/workflow/work_orders",
        data={"property_id": str(seeded["obj"].id), "title": "Anon"},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_ungueltiger_uebergang_422(client, seeded):
    c = _logged_in_client(client)
    r = c.post(
        f"/api/workflow/work_orders/{seeded['a1'].id}/status",
        data={"to_status": "ABGERECHNET"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_voller_durchlauf_ueber_api(client, app_user):
    obj = property_service.create_property(
        app_user.id, name="Durchlauf", property_type="WEG",
        street="W", postal_code="1", city="Berlin",
    )
    auftraggeber = identity_service.create_person(
        app_user.id, first_name="Petra", last_name="Prinzipal"
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Kompletter Auftrag"
    )
    c = _logged_in_client(client)
    oid = order.id

    assert c.post(
        f"/api/workflow/work_orders/{oid}/evidence",
        data={"reference": "Auftrag per E-Mail"},
        content_type="application/json",
    ).status_code == 200
    assert c.post(
        f"/api/workflow/work_orders/{oid}/responsibility",
        data={"scope": "COMMON_PROPERTY"},
        content_type="application/json",
    ).status_code == 200
    assert c.post(
        f"/api/workflow/work_orders/{oid}/parties",
        data={"party_id": str(auftraggeber.id), "role": "PRINCIPAL", "is_primary": True},
        content_type="application/json",
    ).status_code == 201

    for to_status in ["FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
                      "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"]:
        r = c.post(
            f"/api/workflow/work_orders/{oid}/status",
            data={"to_status": to_status},
            content_type="application/json",
        )
        assert r.status_code == 200, (to_status, r.content)

    body = c.get(f"/api/workflow/work_orders/{oid}").json()
    assert body["status"] == "KAUFMAENNISCH_GEPRUEFT"
    roles = {p["role"] for p in body["parties"]}
    assert "PRINCIPAL" in roles
