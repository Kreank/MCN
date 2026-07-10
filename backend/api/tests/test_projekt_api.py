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
    from .conftest import grant_role
    user = User.objects.create_user(username=f"u{uuid.uuid4().hex[:8]}", password="x")
    if with_app_user:
        from db_core.models import AppUser
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
    r = admin_client.get("/api/workflow/projects?page=1&page_size=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


@pytest.mark.django_db
def test_suche_nach_name(admin_client, seeded):
    r = admin_client.get("/api/workflow/projects?q=Fassade")
    names = {i["name"] for i in r.json()["items"]}
    assert names == {"Fassade Ost"}


@pytest.mark.django_db
def test_suche_nach_nummer(admin_client, seeded):
    r = admin_client.get("/api/workflow/projects?q=P-")
    assert r.json()["total"] == 2


@pytest.mark.django_db
def test_detail_mit_liegenschaften_und_vorgaengen(admin_client, seeded):
    r = admin_client.get(f"/api/workflow/projects/{seeded['p1'].id}")
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
def test_detail_404(admin_client, seeded):
    r = admin_client.get(f"/api/workflow/projects/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_vorgang_detail_mit_verlauf(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="V-Objekt", property_type="WEG",
        street="S", postal_code="1", city="Berlin",
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Heizung",
    )
    r = admin_client.get(f"/api/workflow/service_cases/{case.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "Heizung"
    assert body["status"] == "NEU"
    assert body["property"]["city"] == "Berlin"
    # Der Initial-Status NEU wird per Trigger protokolliert.
    assert len(body["history"]) >= 1
    assert body["history"][-1]["to_status"] == "NEU"


@pytest.mark.django_db
def test_vorgang_detail_404(admin_client, seeded):
    r = admin_client.get(f"/api/workflow/service_cases/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.django_db
def test_projekt_cockpit_log_und_checklisten(admin_client, app_user):
    p = projekt_service.create_project(app_user.id, name="Cockpit-Projekt")
    projekt_service.add_project_log(
        app_user.id, project_id=p.id, category="NOTIZ", entry="Erster Eintrag"
    )
    projekt_service.create_checklist(
        app_user.id, project_id=p.id, name="Start", items=["A", "B"]
    )
    log = admin_client.get(f"/api/workflow/projects/{p.id}/log").json()
    assert len(log) == 1
    assert log[0]["entry"] == "Erster Eintrag"
    assert log[0]["created_by"] == app_user.display_name

    cls = admin_client.get(f"/api/workflow/projects/{p.id}/checklists").json()
    assert len(cls) == 1
    assert cls[0]["name"] == "Start"
    assert len(cls[0]["items"]) == 2
    assert cls[0]["items"][0]["done"] is False


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
def test_create_ohne_login_abgelehnt(anonymous_client, db):
    r = anonymous_client.post(
        "/api/workflow/projects", data={"name": "Anon"},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


# --- Cockpit-Schreib-Endpoints: Logbuch, Checkliste, Vorgang ---------------

@pytest.mark.django_db
def test_add_project_log_happy(admin_client, seeded):
    r = admin_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/log",
        data={"entry": "Kunde angerufen", "category": "ANRUF"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["entry"] == "Kunde angerufen"
    assert body["category"] == "ANRUF"
    assert body["created_by"]  # created_by wird auf den Akteur gesetzt


@pytest.mark.django_db
def test_add_project_log_ungueltige_kategorie_422(admin_client, seeded):
    r = admin_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/log",
        data={"entry": "x", "category": "QUATSCH"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_add_project_log_unbekanntes_projekt_404(admin_client, seeded):
    r = admin_client.post(
        f"/api/workflow/projects/{uuid.uuid4()}/log",
        data={"entry": "x"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_add_project_log_monteur_403_fail_closed(client_with_role, seeded):
    """add_project_log nutzt `require` (AENDERN): Monteur hat nur Scope 'EIGENE',
    der Endpunkt wertet ihn nicht aus → fail-closed 403."""
    c = client_with_role("MONTEUR")
    r = c.post(
        f"/api/workflow/projects/{seeded['p1'].id}/log",
        data={"entry": "x"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_create_checklist_happy(admin_client, seeded):
    r = admin_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/checklists",
        data={"name": "Abnahme", "items": ["Dach", "Rinne"]},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["name"] == "Abnahme"
    assert len(body["items"]) == 2
    assert body["items"][0]["done"] is False


@pytest.mark.django_db
def test_create_checklist_leerer_name_422(admin_client, seeded):
    r = admin_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/checklists",
        data={"name": "  "},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_checklist_ohne_recht_403(client_with_role, seeded):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"/api/workflow/projects/{seeded['p1'].id}/checklists",
        data={"name": "Abnahme"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_create_service_case_happy(admin_client, seeded):
    r = admin_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/service_cases",
        data={"property_id": str(seeded["obj"].id), "subject": "Wasserschaden"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["subject"] == "Wasserschaden"
    assert body["status"] == "NEU"
    # der Vorgang erscheint jetzt im Projektdetail
    detail = admin_client.get(f"/api/workflow/projects/{seeded['p1'].id}").json()
    assert any(c["subject"] == "Wasserschaden" for c in detail["service_cases"])


@pytest.mark.django_db
def test_create_service_case_ungueltige_prioritaet_422(admin_client, seeded):
    r = admin_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/service_cases",
        data={
            "property_id": str(seeded["obj"].id),
            "subject": "X",
            "priority": "SOFORT",
        },
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_create_service_case_ohne_recht_403(client_with_role, seeded):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"/api/workflow/projects/{seeded['p1'].id}/service_cases",
        data={"property_id": str(seeded["obj"].id), "subject": "X"},
        content_type="application/json",
    )
    assert r.status_code == 403


# --- Vorgangs-Statuswechsel: transitions + status --------------------------

def _neuer_vorgang(app_user):
    obj = property_service.create_property(
        app_user.id, name="Statusobjekt", property_type="WEG",
        street="S", postal_code="1", city="Berlin",
    )
    return projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Statusvorgang",
    )


@pytest.mark.django_db
def test_transitions_endpoint_liefert_naechste_status(admin_client, app_user):
    case = _neuer_vorgang(app_user)
    r = admin_client.get(f"/api/workflow/service_cases/{case.id}/transitions")
    assert r.status_code == 200
    body = r.json()
    by = {t["to_status"]: t for t in body}
    assert set(by) == {"IN_PRUEFUNG", "ABGELEHNT"}
    assert by["IN_PRUEFUNG"]["label"] == "In Prüfung"
    assert by["IN_PRUEFUNG"]["reason_required"] is False
    assert by["IN_PRUEFUNG"]["recht"] == "AENDERN"
    assert by["ABGELEHNT"]["reason_required"] is True


@pytest.mark.django_db
def test_transitions_endpoint_unbekannter_vorgang_404(admin_client, seeded):
    r = admin_client.get(f"/api/workflow/service_cases/{uuid.uuid4()}/transitions")
    assert r.status_code == 404


@pytest.mark.django_db
def test_status_gueltiger_uebergang(admin_client, app_user):
    case = _neuer_vorgang(app_user)
    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/status",
        data={"to_status": "IN_PRUEFUNG"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "IN_PRUEFUNG"
    # Der Statusverlauf zeigt den neuen Eintrag.
    assert any(
        h["from_status"] == "NEU" and h["to_status"] == "IN_PRUEFUNG"
        for h in body["history"]
    )


@pytest.mark.django_db
def test_status_begruendungspflichtig_ohne_grund_422(admin_client, app_user):
    case = _neuer_vorgang(app_user)
    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/status",
        data={"to_status": "ABGELEHNT"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_status_ungueltiger_uebergang_422(admin_client, app_user):
    case = _neuer_vorgang(app_user)
    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/status",
        data={"to_status": "BEAUFTRAGT"},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_status_unbekannter_vorgang_404(admin_client, seeded):
    r = admin_client.post(
        f"/api/workflow/service_cases/{uuid.uuid4()}/status",
        data={"to_status": "IN_PRUEFUNG"},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_status_freigabe_uebergang_ohne_freigeben_recht_403(
    client_with_role, app_user
):
    """FREIGABE_AUSSTEHEND → BEAUFTRAGT verlangt workflow.FREIGEBEN. DISPOSITION
    hat AENDERN (Scope ALLE), aber kein FREIGEBEN → 403, obwohl der Übergang
    fachlich gültig wäre."""
    case = _neuer_vorgang(app_user)
    # Als Akteur mit vollen Rechten in FREIGABE_AUSSTEHEND bringen.
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=case.id, to_status="IN_PRUEFUNG"
    )
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=case.id, to_status="FREIGABE_AUSSTEHEND"
    )
    c = client_with_role("DISPOSITION")
    r = c.post(
        f"/api/workflow/service_cases/{case.id}/status",
        data={"to_status": "BEAUFTRAGT"},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_status_freigabe_uebergang_mit_freigeben_recht(admin_client, app_user):
    """Der Freigabe-Übergang gelingt mit FREIGEBEN-Recht (ADMINISTRATION)."""
    case = _neuer_vorgang(app_user)
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=case.id, to_status="IN_PRUEFUNG"
    )
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=case.id, to_status="FREIGABE_AUSSTEHEND"
    )
    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/status",
        data={"to_status": "BEAUFTRAGT"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "BEAUFTRAGT"
