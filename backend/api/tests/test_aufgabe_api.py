"""API-Tests der Aufgaben-Endpoints über den Django-Test-Client."""
import uuid

import pytest

from django.contrib.auth import get_user_model

from db_core.models import AppUser, Task
from db_core.services import aufgabe as aufgabe_service
from db_core.services import projekt as projekt_service

User = get_user_model()


@pytest.fixture
def seeded(app_user):
    p = projekt_service.create_project(app_user.id, name="Projekt Aufgaben")
    t_open = aufgabe_service.create_task(
        app_user.id, title="Offene Aufgabe", project_id=p.id
    )
    t_done = aufgabe_service.create_task(app_user.id, title="Erledigte Aufgabe")
    aufgabe_service.complete_task(app_user.id, t_done.id)
    t_disc = aufgabe_service.create_task(app_user.id, title="Verworfene Aufgabe")
    aufgabe_service.discard_task(app_user.id, t_disc.id)
    return {"app_user": app_user, "project": p, "open": t_open, "done": t_done, "disc": t_disc}


def _logged_in_client(client, *, with_app_user=True):
    from .conftest import grant_role
    user = User.objects.create_user(username=f"u{uuid.uuid4().hex[:8]}", password="x")
    if with_app_user:
        au = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Login", status="ACTIVE", version=1
        )
        user.app_user_id = au.id
        user.save()
        grant_role(au.id, "ADMINISTRATION")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_liste_blendet_verworfene_aus(admin_client, seeded):
    r = admin_client.get("/api/workflow/tasks")
    assert r.status_code == 200
    titles = {i["title"] for i in r.json()["items"]}
    assert "Offene Aufgabe" in titles
    assert "Erledigte Aufgabe" in titles
    assert "Verworfene Aufgabe" not in titles


@pytest.mark.django_db
def test_offene_vor_erledigten(admin_client, seeded):
    # Ohne Statusfilter: OFFEN muss vor ERLEDIGT stehen (Rang, nicht alphabetisch).
    r = admin_client.get("/api/workflow/tasks")
    statuses = [i["status"] for i in r.json()["items"]]
    assert statuses.index("OFFEN") < statuses.index("ERLEDIGT")


@pytest.mark.django_db
def test_status_filter_verworfen(admin_client, seeded):
    r = admin_client.get("/api/workflow/tasks?status=VERWORFEN")
    titles = {i["title"] for i in r.json()["items"]}
    assert titles == {"Verworfene Aufgabe"}


@pytest.mark.django_db
def test_projektfilter(admin_client, seeded):
    r = admin_client.get(f"/api/workflow/tasks?project_id={seeded['project'].id}")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Offene Aufgabe"
    assert body["items"][0]["project"]["name"] == "Projekt Aufgaben"


@pytest.mark.django_db
def test_create_eingeloggt(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/workflow/tasks", data={"title": "Neue Aufgabe"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["title"] == "Neue Aufgabe"
    assert body["status"] == "OFFEN"
    assert Task.objects.filter(id=body["id"]).exists()


@pytest.mark.django_db
def test_create_leerer_titel_422(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(
        "/api/workflow/tasks", data={"title": "  "}, content_type="application/json"
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_ohne_login_abgelehnt(anonymous_client, db):
    r = anonymous_client.post(
        "/api/workflow/tasks", data={"title": "Anon"}, content_type="application/json"
    )
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_complete_action(client, seeded):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(f"/api/workflow/tasks/{seeded['open'].id}/complete")
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "ERLEDIGT"
    assert r.json()["completed_at"] is not None


@pytest.mark.django_db
def test_complete_404(client, db):
    c = _logged_in_client(client, with_app_user=True)
    r = c.post(f"/api/workflow/tasks/{uuid.uuid4()}/complete")
    assert r.status_code == 404
