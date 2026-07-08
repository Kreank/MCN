"""API-Tests der Projekt-Endpoints über den Django-Test-Client."""
import uuid

import pytest

from django.contrib.auth import get_user_model

from db_core.models import Project
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service

User = get_user_model()


@pytest.fixture
def seeded(app_user):
    obj = property_service.create_property(
        app_user.id, name="Wohnhaus Ost", property_type="WEG",
        street="Ostweg", house_number="3", postal_code="10115", city="Berlin",
    )
    p1 = projekt_service.create_project(
        app_user.id, name="Fassade Ost", property_ids=[obj.id]
    )
    projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Riss in Fassade", project_id=p1.id,
    )
    p2 = projekt_service.create_project(app_user.id, name="Kellerentwässerung")
    return {"app_user": app_user, "obj": obj, "p1": p1, "p2": p2}


def _logged_in_client(client, *, with_app_user=True):
    user = User.objects.create_user(username=f"u{uuid.uuid4().hex[:8]}", password="x")
    if with_app_user:
        from db_core.models import AppUser
        au = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Login", status="ACTIVE", version=1
        )
        user.app_user_id = au.id
        user.save()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_liste_und_pagination(client, seeded):
    r = client.get("/api/workflow/projects?page=1&page_size=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


@pytest.mark.django_db
def test_suche_nach_name(client, seeded):
    r = client.get("/api/workflow/projects?q=Fassade")
    names = {i["name"] for i in r.json()["items"]}
    assert names == {"Fassade Ost"}


@pytest.mark.django_db
def test_suche_nach_nummer(client, seeded):
    r = client.get("/api/workflow/projects?q=P-")
    assert r.json()["total"] == 2


@pytest.mark.django_db
def test_detail_mit_liegenschaften_und_vorgaengen(client, seeded):
    r = client.get(f"/api/workflow/projects/{seeded['p1'].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OPEN"
    assert len(body["properties"]) == 1
    assert body["properties"][0]["name"] == "Wohnhaus Ost"
    assert body["properties"][0]["city"] == "Berlin"
    assert len(body["service_cases"]) == 1
    assert body["service_cases"][0]["subject"] == "Riss in Fassade"
    assert body["service_cases"][0]["status"] == "NEU"


@pytest.mark.django_db
def test_detail_404(client, seeded):
    r = client.get(f"/api/workflow/projects/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_create_eingeloggt(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/workflow/projects",
        data={"name": "Neubau Halle"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["name"] == "Neubau Halle"
    assert body["project_number"].startswith("P-")
    assert Project.objects.filter(id=body["id"]).exists()


@pytest.mark.django_db
def test_create_leerer_name_422(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/workflow/projects", data={"name": "  "},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_ohne_login_abgelehnt(client, db):
    r = client.post(
        "/api/workflow/projects", data={"name": "Anon"},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)
