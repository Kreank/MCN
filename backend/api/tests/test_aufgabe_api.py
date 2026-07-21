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


# --- Bearbeiten (PATCH) ----------------------------------------------------

@pytest.mark.django_db
def test_patch_aendert_felder(admin_client, seeded):
    task = seeded["open"]
    r = admin_client.patch(
        f"/api/workflow/tasks/{task.id}",
        data={
            "title": "Neuer Titel",
            "description": "Frische Beschreibung",
            "due_date": "2026-12-24",
        },
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["title"] == "Neuer Titel"
    assert body["description"] == "Frische Beschreibung"
    assert body["due_date"] == "2026-12-24"
    task.refresh_from_db()
    assert task.title == "Neuer Titel"


@pytest.mark.django_db
def test_patch_nur_gesendete_felder(admin_client, seeded):
    """exclude_unset: ein nicht gesendetes Feld bleibt unverändert."""
    task = seeded["open"]
    original_project = task.project_id
    r = admin_client.patch(
        f"/api/workflow/tasks/{task.id}",
        data={"title": "Nur Titel geändert"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    task.refresh_from_db()
    assert task.title == "Nur Titel geändert"
    # Projektbezug (nicht gesendet) unangetastet.
    assert task.project_id == original_project


@pytest.mark.django_db
def test_patch_setzt_zuweisung_und_kontakt(admin_client, seeded):
    from db_core.services import identity as identity_service

    empf = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Empfänger", status="ACTIVE", version=1
    )
    partei = identity_service.create_person(
        seeded["app_user"].id, first_name="Klara", last_name="Kontakt"
    )
    task = seeded["open"]
    r = admin_client.patch(
        f"/api/workflow/tasks/{task.id}",
        data={
            "assigned_to_user_id": str(empf.id),
            "party_id": str(partei.id),
        },
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["assigned_to"]["id"] == str(empf.id)
    assert body["party"]["id"] == str(partei.id)


@pytest.mark.django_db
def test_patch_unbekannter_benutzer_422(admin_client, seeded):
    task = seeded["open"]
    r = admin_client.patch(
        f"/api/workflow/tasks/{task.id}",
        data={"assigned_to_user_id": str(uuid.uuid4())},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_patch_unbekanntes_projekt_422(admin_client, seeded):
    task = seeded["open"]
    r = admin_client.patch(
        f"/api/workflow/tasks/{task.id}",
        data={"project_id": str(uuid.uuid4())},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_patch_leerer_titel_422(admin_client, seeded):
    task = seeded["open"]
    r = admin_client.patch(
        f"/api/workflow/tasks/{task.id}",
        data={"title": "  "},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_patch_404_auf_unbekannter_aufgabe(admin_client, db):
    r = admin_client.patch(
        f"/api/workflow/tasks/{uuid.uuid4()}",
        data={"title": "Egal"},
        content_type="application/json",
    )
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_patch_beruehrt_status_nicht(admin_client, seeded):
    """Bearbeiten ist kein Statuswechsel: eine erledigte Aufgabe bleibt erledigt."""
    task = seeded["done"]
    task.refresh_from_db()
    assert task.status == "ERLEDIGT"
    r = admin_client.patch(
        f"/api/workflow/tasks/{task.id}",
        data={"title": "Titel trotz erledigt geändert"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "ERLEDIGT"
    task.refresh_from_db()
    assert task.status == "ERLEDIGT"
    assert task.completed_at is not None


@pytest.mark.django_db
def test_patch_ohne_login_abgelehnt(anonymous_client, seeded):
    r = anonymous_client.patch(
        f"/api/workflow/tasks/{seeded['open'].id}",
        data={"title": "Anon"},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


# --- Auftragsbezug (Befund D2, Migration 0129) ------------------------------


@pytest.fixture
def auftrag(app_user):
    from db_core.services import auftrag as auftrag_service
    from db_core.services import property as property_service

    prop = property_service.create_property(
        app_user.id, name="Aufgaben-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    return auftrag_service.create_work_order(
        app_user.id, property_id=prop.id, title="Heizung tropft"
    )


@pytest.mark.django_db
def test_aufgabe_am_auftrag_anlegen(admin_client, auftrag):
    """Befund D2: „Beim Kunden anrufen wegen Ersatzteil" gehört an den Auftrag."""
    r = admin_client.post(
        "/api/workflow/tasks",
        data={"title": "Ersatzteil bestellen", "work_order_id": str(auftrag.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["work_order"]["id"] == str(auftrag.id)
    assert body["work_order"]["order_number"] == auftrag.order_number


@pytest.mark.django_db
def test_bezuege_sind_kombinierbar(admin_client, app_user, auftrag):
    """Der Kern der Modellentscheidung: KEIN Exklusivitäts-CHECK.

    `content.file_link` verlangt genau ein Ziel, `workflow.task` ausdrücklich
    „und/oder" (0005). Eine Aufgabe am Auftrag hängt fast immer auch am Kunden,
    den man deswegen anruft.
    """
    from db_core.services import identity as identity_service
    from db_core.services import projekt as projekt_service

    kunde = identity_service.create_person(
        app_user.id, first_name="Erika", last_name="Meyer"
    )
    projekt = projekt_service.create_project(app_user.id, name="Sanierung")

    r = admin_client.post(
        "/api/workflow/tasks",
        data={
            "title": "Rückruf",
            "work_order_id": str(auftrag.id),
            "party_id": str(kunde.id),
            "project_id": str(projekt.id),
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["work_order"] is not None
    assert body["party"] is not None
    assert body["project"] is not None


@pytest.mark.django_db
def test_filter_nach_auftrag(admin_client, auftrag):
    admin_client.post(
        "/api/workflow/tasks",
        data={"title": "Am Auftrag", "work_order_id": str(auftrag.id)},
        content_type="application/json",
    )
    admin_client.post(
        "/api/workflow/tasks",
        data={"title": "Ohne Auftrag"},
        content_type="application/json",
    )
    r = admin_client.get(f"/api/workflow/tasks?work_order_id={auftrag.id}")
    assert r.status_code == 200
    titel = [t["title"] for t in r.json()["items"]]
    assert titel == ["Am Auftrag"]


@pytest.mark.django_db
def test_auftragsbezug_nachtraeglich_setzen_und_loesen(admin_client, auftrag):
    r = admin_client.post(
        "/api/workflow/tasks",
        data={"title": "Später zuordnen"},
        content_type="application/json",
    )
    task_id = r.json()["id"]

    r = admin_client.patch(
        f"/api/workflow/tasks/{task_id}",
        data={"work_order_id": str(auftrag.id)},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["work_order"]["id"] == str(auftrag.id)

    # `null` löst den Bezug (Server: exclude_unset trennt „nicht gesendet"
    # von „auf null gesetzt").
    r = admin_client.patch(
        f"/api/workflow/tasks/{task_id}",
        data={"work_order_id": None},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["work_order"] is None


@pytest.mark.django_db
def test_unbekannter_auftrag_ist_422(admin_client):
    import uuid as _uuid

    r = admin_client.post(
        "/api/workflow/tasks",
        data={"title": "Ins Leere", "work_order_id": str(_uuid.uuid4())},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Auftrag" in r.json()["detail"]
