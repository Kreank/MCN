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
    """FREIGABE_AUSSTEHEND → BEAUFTRAGT verlangt workflow.FREIGEBEN: AENDERN
    allein genügt nicht, obwohl der Übergang fachlich gültig wäre.

    DISPOSITION diente hier als Beispielrolle „AENDERN ja, FREIGEBEN nein" —
    seit Migration 0122 hat sie FREIGEBEN (der Anruf-Durchstich legt Aufträge
    telefonisch an und gibt sie frei). Statt die Rolle zu tauschen, wird das
    Recht hier gezielt entzogen: Der Test prüft dann genau eine Variable und
    bleibt gültig, egal wie die Startmatrix künftig aussieht.
    """
    from django.db import connection

    case = _neuer_vorgang(app_user)
    # Als Akteur mit vollen Rechten in FREIGABE_AUSSTEHEND bringen.
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=case.id, to_status="IN_PRUEFUNG"
    )
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=case.id, to_status="FREIGABE_AUSSTEHEND"
    )
    c = client_with_role("DISPOSITION")
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE security.role_permission
               SET allowed = false
             WHERE role_code = 'DISPOSITION'
               AND module    = 'workflow'
               AND action    = 'FREIGEBEN'
            """
        )
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


# --- Vorgangs-Board: GET /api/workflow/service_cases -----------------------

def _vorgang_unter_projekt(app_user, *, subject="Boardvorgang", projektname="Boardprojekt"):
    obj = property_service.create_property(
        app_user.id, name="Boardobjekt", property_type="WEG",
        street="B", postal_code="1", city="Berlin",
    )
    projekt = projekt_service.create_project(
        app_user.id, name=projektname, property_ids=[obj.id]
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject=subject, project_id=projekt.id,
    )
    return projekt, case


@pytest.mark.django_db
def test_board_liefert_vorgaenge_mit_status_und_projekt(admin_client, app_user):
    projekt, case = _vorgang_unter_projekt(app_user)
    r = admin_client.get("/api/workflow/service_cases")
    assert r.status_code == 200, r.content
    body = r.json()
    # Spalten kommen aus dem Statuskatalog (7 Status, nach sort_order).
    stati = [c["status"] for c in body["columns"]]
    assert stati == [
        "NEU", "IN_PRUEFUNG", "RUECKFRAGE", "FREIGABE_AUSSTEHEND",
        "BEAUFTRAGT", "ABGESCHLOSSEN", "ABGELEHNT",
    ]
    assert body["columns"][0]["label"] == "Neu"
    assert body["columns"][-1]["is_terminal"] is True
    assert body["columns"][-2]["is_terminal"] is True
    assert body["columns"][0]["is_terminal"] is False
    # Der Vorgang trägt Status und Projektbezug.
    item = next(i for i in body["items"] if i["id"] == str(case.id))
    assert item["status"] == "NEU"
    assert item["project_id"] == str(projekt.id)
    assert item["project_name"] == "Boardprojekt"
    assert item["case_number"] == case.case_number


@pytest.mark.django_db
def test_board_filter_project_id(admin_client, app_user):
    p1, c1 = _vorgang_unter_projekt(app_user, projektname="Projekt A")
    p2, c2 = _vorgang_unter_projekt(app_user, projektname="Projekt B")
    r = admin_client.get(f"/api/workflow/service_cases?project_id={p1.id}")
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {str(c1.id)}


@pytest.mark.django_db
def test_board_filter_status(admin_client, app_user):
    _p, offen = _vorgang_unter_projekt(app_user)
    _p2, geprueft = _vorgang_unter_projekt(app_user)
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=geprueft.id, to_status="IN_PRUEFUNG"
    )
    r = admin_client.get("/api/workflow/service_cases?status=IN_PRUEFUNG")
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {str(geprueft.id)}


@pytest.mark.django_db
def test_board_freitext_q(admin_client, app_user):
    _p1, treffer = _vorgang_unter_projekt(app_user, subject="Heizung defekt")
    _p2, _andere = _vorgang_unter_projekt(app_user, subject="Fenster klemmt")
    r = admin_client.get("/api/workflow/service_cases?q=Heizung")
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {str(treffer.id)}
    # Auch über die Vorgangsnummer.
    r2 = admin_client.get(f"/api/workflow/service_cases?q={treffer.case_number}")
    assert {i["id"] for i in r2.json()["items"]} == {str(treffer.id)}


@pytest.mark.django_db
def test_board_terminal_default_ausgeblendet(admin_client, app_user):
    _p, offen = _vorgang_unter_projekt(app_user, subject="Offener Vorgang")
    _p2, abgelehnt = _vorgang_unter_projekt(app_user, subject="Abzulehnen")
    projekt_service.advance_service_case_status(
        app_user.id, service_case_id=abgelehnt.id, to_status="ABGELEHNT",
        reason="Nicht zuständig.",
    )
    # Default: der abgelehnte Vorgang ist nicht geladen.
    r = admin_client.get("/api/workflow/service_cases")
    ids = {i["id"] for i in r.json()["items"]}
    assert str(offen.id) in ids
    assert str(abgelehnt.id) not in ids
    # include_terminal=true lädt ihn mit.
    r2 = admin_client.get("/api/workflow/service_cases?include_terminal=true")
    assert str(abgelehnt.id) in {i["id"] for i in r2.json()["items"]}
    # Ein expliziter status-Filter hat Vorrang und zeigt Endspalten-Vorgänge auch
    # ohne include_terminal.
    r3 = admin_client.get("/api/workflow/service_cases?status=ABGELEHNT")
    assert {i["id"] for i in r3.json()["items"]} == {str(abgelehnt.id)}


@pytest.mark.django_db
def test_board_nur_offen_blendet_beauftragt_aus(admin_client, app_user):
    """Eingangskorb (nur_offen): ein zum Auftrag gemachter (BEAUFTRAGT) Vorgang hat
    den Eingang verlassen — anders als der Default, der ihn noch zeigt. Das Kanban
    (ohne nur_offen) behält BEAUFTRAGT als Spalte."""
    _p, offen = _vorgang_unter_projekt(app_user, subject="Wartet auf Entscheidung")
    _p2, beauftragt = _vorgang_unter_projekt(app_user, subject="Schon beauftragt")
    for to_status in ("IN_PRUEFUNG", "FREIGABE_AUSSTEHEND"):
        projekt_service.advance_service_case_status(
            app_user.id, service_case_id=beauftragt.id, to_status=to_status
        )
    rb = admin_client.post(
        f"/api/workflow/service_cases/{beauftragt.id}/status",
        data={"to_status": "BEAUFTRAGT"},
        content_type="application/json",
    )
    assert rb.status_code == 200, rb.content

    # Default (Kanban): BEAUFTRAGT bleibt sichtbar.
    ids_default = {
        i["id"] for i in admin_client.get("/api/workflow/service_cases").json()["items"]
    }
    assert str(beauftragt.id) in ids_default

    # nur_offen=true (Eingang): BEAUFTRAGT ist raus, der offene bleibt.
    ids_offen = {
        i["id"]
        for i in admin_client.get(
            "/api/workflow/service_cases?nur_offen=true"
        ).json()["items"]
    }
    assert str(offen.id) in ids_offen
    assert str(beauftragt.id) not in ids_offen

    # Expliziter Status-Filter hat Vorrang — BEAUFTRAGT sichtbar trotz nur_offen.
    r3 = admin_client.get(
        "/api/workflow/service_cases?nur_offen=true&status=BEAUFTRAGT"
    )
    assert {i["id"] for i in r3.json()["items"]} == {str(beauftragt.id)}


@pytest.mark.django_db
def test_board_zeigt_dem_monteur_ohne_objekt_keine_zeile(client_with_role, app_user):
    """Objektsicht (0099): Das Board wertet den row_scope jetzt aus — es zeigt die
    Vorgänge **meiner Objekte**. Ein MONTEUR ohne jeden Einsatz hat kein Objekt und
    sieht deshalb **null** Zeilen (200 mit leerer Liste, kein 403).

    Das ist die schärfere Probe als das frühere 403: Sie beweist nicht, dass der
    Endpunkt zumacht, sondern dass er **filtert** — ein `require_scoped` ohne Filter
    fiele hier auf (die Liste wäre nicht leer)."""
    _vorgang_unter_projekt(app_user)
    c = client_with_role("MONTEUR")
    r = c.get("/api/workflow/service_cases")
    assert r.status_code == 200, r.content
    assert r.json()["items"] == []
    assert r.json()["total"] == 0


@pytest.mark.django_db
def test_board_ohne_login_401(anonymous_client, app_user):
    _vorgang_unter_projekt(app_user)
    r = anonymous_client.get("/api/workflow/service_cases")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_board_keine_n_plus_1(admin_client, app_user):
    """Die Query-Zahl ist unabhängig von der Zeilenzahl (select_related('project')
    + zeilenzahl-unabhängiger Spalten-/Count-Query)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    # Sechs Vorgänge unter je eigenem Projekt (verschiedene FKs).
    for i in range(6):
        _vorgang_unter_projekt(app_user, projektname=f"NP-Projekt {i}")

    def _queries(url):
        with CaptureQueriesContext(connection) as ctx:
            resp = admin_client.get(url)
            assert resp.status_code == 200
        return len(ctx.captured_queries)

    q_eine = _queries("/api/workflow/service_cases?page_size=1")
    q_alle = _queries("/api/workflow/service_cases?page_size=50")
    assert q_eine == q_alle, (
        f"N+1: {q_eine} Queries bei 1 Zeile vs. {q_alle} bei vielen Zeilen"
    )


# --- Vorgang ohne Projekt: POST /api/workflow/service_cases ----------------

@pytest.mark.django_db
def test_service_case_standalone_happy(admin_client, seeded):
    """Vorgang ohne Projekt anlegen: 201, Status NEU, project_id bleibt NULL."""
    from db_core.models import ServiceCase

    r = admin_client.post(
        "/api/workflow/service_cases",
        data={"property_id": str(seeded["obj"].id), "subject": "Therme defekt"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["subject"] == "Therme defekt"
    assert body["status"] == "NEU"
    case = ServiceCase.objects.get(id=body["id"])
    assert case.project_id is None


@pytest.mark.django_db
def test_service_case_standalone_leerer_subject_422(admin_client, seeded):
    r = admin_client.post(
        "/api/workflow/service_cases",
        data={"property_id": str(seeded["obj"].id), "subject": "  "},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_service_case_standalone_ohne_recht_403(client_with_role, seeded):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/workflow/service_cases",
        data={"property_id": str(seeded["obj"].id), "subject": "X"},
        content_type="application/json",
    )
    assert r.status_code == 403


# --- Schnellerfassung: POST /api/workflow/quick-intake ---------------------

def _quick_intake_payload(**overrides):
    payload = {
        "person": {
            "salutation": "Herr",
            "first_name": "Max",
            "last_name": "Mustermann",
        },
        "contact": {"phone": "030 123456", "email": "max@example.de"},
        "property": {
            "street": "Hauptstraße",
            "house_number": "5",
            "postal_code": "10115",
            "city": "Berlin",
        },
        "meldung": {"subject": "Therme Fehler F28", "priority": "DRINGEND"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_quick_intake_happy(admin_client, db):
    """Ein Durchstich legt Person + Liegenschaft (EFH) + Eigentümer-Rolle +
    Kontaktwege + Vorgang (ohne Projekt) an."""
    from db_core.models import (
        ContactPoint,
        Party,
        Property,
        PropertyPartyRole,
        ServiceCase,
    )

    r = admin_client.post(
        "/api/workflow/quick-intake",
        data=_quick_intake_payload(),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()

    party = Party.objects.get(id=body["party_id"])
    assert party.party_type == "PERSON"
    assert party.display_name == "Max Mustermann"

    prop = Property.objects.get(id=body["property_id"])
    assert prop.property_type == "EINFAMILIENHAUS"
    # name wird nicht abgefragt → aus der Adresse abgeleitet.
    assert "Hauptstraße" in prop.name

    assert PropertyPartyRole.objects.filter(
        property_id=prop.id, party_id=party.id, role="PROPERTY_OWNER"
    ).exists()

    typen = set(
        ContactPoint.objects.filter(party_id=party.id).values_list(
            "contact_type", flat=True
        )
    )
    assert typen == {"PHONE", "EMAIL"}

    case = ServiceCase.objects.get(id=body["service_case"]["id"])
    assert case.status == "NEU"
    assert case.project_id is None
    assert case.reported_by_party_id == party.id
    assert case.priority == "DRINGEND"
    assert body["service_case"]["case_number"].startswith("V-")


@pytest.mark.django_db
def test_quick_intake_ohne_kontakt(admin_client, db):
    """contact ist optional; ohne Telefon/E-Mail entsteht kein Kontaktweg."""
    from db_core.models import ContactPoint

    payload = _quick_intake_payload(contact=None)
    r = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r.status_code == 201, r.content
    party_id = r.json()["party_id"]
    assert not ContactPoint.objects.filter(party_id=party_id).exists()


@pytest.mark.django_db
def test_quick_intake_rollback_bei_ungueltiger_meldung(admin_client, db):
    """Atomarität: schlägt der letzte Schritt (Vorgang) fehl, bleiben KEINE
    Waisen (Person/Liegenschaft) zurück — alles wird zurückgerollt."""
    from db_core.models import Party, Property

    parties_vorher = Party.objects.count()
    props_vorher = Property.objects.count()

    payload = _quick_intake_payload(
        meldung={"subject": "   ", "priority": "NORMAL"}  # leer → ValueError zuletzt
    )
    r = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r.status_code == 422, r.content
    assert Party.objects.count() == parties_vorher
    assert Property.objects.count() == props_vorher


@pytest.mark.django_db
def test_quick_intake_ohne_recht_403(client_with_role, db):
    c = client_with_role("NUR_LESEN")
    r = c.post(
        "/api/workflow/quick-intake",
        data=_quick_intake_payload(),
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_quick_intake_leerer_nachname_422_statt_500(admin_client, db):
    """Nur-Leerzeichen-Nachname ist ein Fachfehler → 422 (nicht 500), und der
    erste Schritt scheitert vor jeder Anlage: keine Waisen.

    Seit Migration 0125 gilt das nur noch für den NACHNAMEN (Befund B1/B3).
    """
    from db_core.models import Party

    parties_vorher = Party.objects.count()
    payload = _quick_intake_payload(
        person={"first_name": "Erika", "last_name": "   "}
    )
    r = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r.status_code == 422, r.content
    assert Party.objects.count() == parties_vorher


@pytest.mark.django_db
def test_quick_intake_gibt_dem_melder_KEINE_adresse(admin_client, db):
    """Befund F4 wird bewusst NICHT automatisch behoben — dieser Test hält das fest.

    Die Anschrift landet nur an der Liegenschaft; der Melder bekommt keine
    `party_address`. Der naheliegende Griff (dieselbe Adresszeile zuordnen) war
    einmal gebaut und wurde zurückgenommen:

    „Liegenschaft neu" belegt, dass das OBJEKT noch nicht erfasst war — nicht,
    dass der Anrufer dort wohnt. Ein Vermieter, der sein Mietobjekt erstmals
    meldet, bekäme dessen Anschrift als Privatadresse. Über
    `beleg._ADDRESS_PREFERENCE` (das bis PRIVATE durchfällt) stünde sie
    anschließend als Empfängeranschrift im Snapshot GoBD-relevanter Belege, wo
    heute ehrlich keine steht — und `excl_party_address_primary` verbaute den
    Platz für die echte Adresse, ohne dass es einen Weg zurück gibt
    (`party_address` kennt außer POST keine Schreiboperation, Befund H3).

    Wird das je umgesetzt, braucht es AP4 (Zuordnungen korrigieren/beenden)
    UND eine ausdrückliche Bestätigung im Dialog. Dann ist dieser Test zu
    ändern — mit Absicht, nicht aus Versehen.
    """
    from db_core.models import PartyAddress

    r = admin_client.post(
        "/api/workflow/quick-intake",
        data=_quick_intake_payload(),
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert not PartyAddress.objects.filter(party_id=r.json()["party_id"]).exists()


@pytest.mark.django_db
def test_quick_intake_ohne_vornamen(admin_client, db):
    """Befund B1: „Frau Özdemir aus der Ahornstraße meldet einen Wasserschaden"
    ist eine vollständige Meldung — der Vorname kommt darin nicht vor.

    Vorher erzwang die Schnellerfassung ihn und provozierte damit erfundene
    Werte wie „X", die anschließend in Anrede und Anschreiben landeten.
    """
    from db_core.models import Party

    payload = _quick_intake_payload(person={"last_name": "Özdemir"})
    r = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r.status_code == 201, r.content
    party = Party.objects.get(display_name="Özdemir")
    assert party.person.first_name is None


@pytest.mark.django_db
def test_quick_intake_bestehende_liegenschaft_dedup(admin_client, db):
    """Dedup: existing_property_id referenziert ein bereits erfasstes Objekt — es
    entsteht KEINE zweite Liegenschaft/Adresse, der neue Vorgang hängt am selben
    Objekt, und der zweite Anrufer wird NICHT als Eigentümer eingetragen (er ist
    Melder, nicht zwingend Eigentümer)."""
    from db_core.models import Property, PropertyPartyRole, ServiceCase

    # 1. Objekt regulär anlegen.
    r1 = admin_client.post(
        "/api/workflow/quick-intake",
        data=_quick_intake_payload(),
        content_type="application/json",
    )
    assert r1.status_code == 201, r1.content
    prop_id = r1.json()["property_id"]
    erster_melder = r1.json()["party_id"]

    props_vorher = Property.objects.count()

    # 2. Zweite Meldung: anderer Anrufer, SELBE (bestehende) Liegenschaft.
    payload = _quick_intake_payload(
        person={"first_name": "Erika", "last_name": "Musterfrau"},
        contact=None,
        property={"existing_property_id": prop_id},
    )
    r2 = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r2.status_code == 201, r2.content
    body = r2.json()

    # Keine neue Liegenschaft entstanden.
    assert Property.objects.count() == props_vorher
    assert body["property_id"] == prop_id

    # Neuer Vorgang hängt an der bestehenden Liegenschaft, neuer Melder.
    case = ServiceCase.objects.get(id=body["service_case"]["id"])
    assert str(case.property_id) == prop_id
    assert str(case.reported_by_party_id) == body["party_id"]
    assert body["party_id"] != erster_melder

    # Der zweite Anrufer ist NICHT Eigentümer; der erste Melder bleibt es.
    assert not PropertyPartyRole.objects.filter(
        property_id=prop_id, party_id=body["party_id"], role="PROPERTY_OWNER"
    ).exists()
    assert PropertyPartyRole.objects.filter(
        property_id=prop_id, party_id=erster_melder, role="PROPERTY_OWNER"
    ).exists()


@pytest.mark.django_db
def test_quick_intake_bestehender_kontakt_dedup(admin_client, db):
    """Dedup Person: existing_party_id setzt einen bereits erfassten Kontakt als
    Melder — es entsteht KEIN zweiter Kontakt; der Vorgang trägt den bestehenden
    als reported_by."""
    from db_core.models import Party, ServiceCase

    # 1. Kontakt regulär anlegen.
    r1 = admin_client.post(
        "/api/workflow/quick-intake",
        data=_quick_intake_payload(),
        content_type="application/json",
    )
    assert r1.status_code == 201, r1.content
    erster_party = r1.json()["party_id"]

    parties_vorher = Party.objects.count()

    # 2. Zweite Meldung: bestehender Kontakt als Melder, neue Liegenschaft.
    payload = _quick_intake_payload(
        person={"existing_party_id": erster_party},
        contact=None,
    )
    r2 = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r2.status_code == 201, r2.content
    body = r2.json()

    # Kein neuer Kontakt entstanden; Melder ist der bestehende.
    assert Party.objects.count() == parties_vorher
    assert body["party_id"] == erster_party
    case = ServiceCase.objects.get(id=body["service_case"]["id"])
    assert str(case.reported_by_party_id) == erster_party


@pytest.mark.django_db
def test_quick_intake_bestehender_kontakt_unbekannt_422(admin_client, db):
    """Eine nicht existierende existing_party_id ist ein Fachfehler → 422 ohne
    Waisen (die neue Liegenschaft wird zurückgerollt)."""
    import uuid

    from db_core.models import Property

    props_vorher = Property.objects.count()
    payload = _quick_intake_payload(
        person={"existing_party_id": str(uuid.uuid4())},
        contact=None,
    )
    r = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r.status_code == 422, r.content
    assert Property.objects.count() == props_vorher


@pytest.mark.django_db
def test_quick_intake_bestehende_liegenschaft_unbekannt_422(admin_client, db):
    """Eine nicht existierende existing_property_id ist ein Fachfehler → 422
    (keine Waisen), nicht 500."""
    import uuid

    from db_core.models import Party

    parties_vorher = Party.objects.count()
    payload = _quick_intake_payload(
        contact=None,
        property={"existing_property_id": str(uuid.uuid4())},
    )
    r = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r.status_code == 422, r.content
    assert Party.objects.count() == parties_vorher


# --- Zum Projekt hochstufen: POST .../service_cases/{id}/promote-to-project -

@pytest.mark.django_db
def test_promote_to_project_haengt_vorgang_und_auftraege_um(admin_client, app_user):
    """Hochstufen legt ein Projekt an und hängt Vorgang UND Auftrag darunter;
    das Projekt führt die Liegenschaft als project_property."""
    from db_core.models import ProjectProperty, ServiceCase, WorkOrder
    from db_core.services import auftrag as auftrag_service

    obj = property_service.create_property(
        app_user.id, name="EFH Musterweg", property_type="EINFAMILIENHAUS",
        street="Musterweg", house_number="1", postal_code="10115", city="Berlin",
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Therme tauschen",
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Thermentausch",
        service_case_id=case.id,
    )
    assert case.project_id is None

    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/promote-to-project",
        data={},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    proj_id = body["id"]
    # Ohne Name übernimmt das Projekt den Vorgangsbetreff.
    assert body["name"] == "Therme tauschen"
    assert body["project_number"].startswith("P-")

    assert str(ServiceCase.objects.get(id=case.id).project_id) == proj_id
    assert str(WorkOrder.objects.get(id=order.id).project_id) == proj_id
    assert ProjectProperty.objects.filter(
        project_id=proj_id, property_id=obj.id
    ).exists()


@pytest.mark.django_db
def test_promote_to_project_mit_eigenem_namen(admin_client, app_user):
    obj = property_service.create_property(
        app_user.id, name="EFH", property_type="EINFAMILIENHAUS",
        street="Weg", postal_code="10115", city="Berlin",
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Klein",
    )
    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/promote-to-project",
        data={"name": "Heizungssanierung 2026"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["name"] == "Heizungssanierung 2026"


@pytest.mark.django_db
def test_promote_to_project_schon_projekt_422(admin_client, seeded):
    """Ein Vorgang, der bereits unter einem Projekt hängt, kann nicht hochgestuft
    werden."""
    from db_core.models import ServiceCase

    case = ServiceCase.objects.filter(project_id=seeded["p1"].id).first()
    assert case is not None
    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/promote-to-project",
        data={},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_promote_to_project_unbekannter_vorgang_404(admin_client, db):
    r = admin_client.post(
        f"/api/workflow/service_cases/{uuid.uuid4()}/promote-to-project",
        data={},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_promote_to_project_ohne_recht_403(client_with_role, app_user):
    obj = property_service.create_property(
        app_user.id, name="EFH", property_type="EINFAMILIENHAUS",
        street="Weg", postal_code="10115", city="Berlin",
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="X",
    )
    c = client_with_role("NUR_LESEN")
    r = c.post(
        f"/api/workflow/service_cases/{case.id}/promote-to-project",
        data={},
        content_type="application/json",
    )
    assert r.status_code == 403


# --- Projektkategorien (Gewerk) ---------------------------------------------

def _neue_kategorie(app_user, *, name, status="AKTIV", sort_order=1):
    """Legt eine workflow.project_category direkt an (kein Service-Endpunkt)."""
    from db_core.db_context import business_transaction
    from db_core.models import ProjectCategory

    with business_transaction(app_user.id):
        return ProjectCategory.objects.create(
            id=uuid.uuid4(),
            name=name,
            sort_order=sort_order,
            status=status,
            version=1,
        )


@pytest.mark.django_db
def test_project_categories_nur_aktive(admin_client, app_user):
    _neue_kategorie(app_user, name="Elektro", status="AKTIV", sort_order=2)
    _neue_kategorie(app_user, name="Alt-Gewerk", status="INAKTIV", sort_order=1)
    r = admin_client.get("/api/workflow/project-categories")
    assert r.status_code == 200, r.content
    namen = [c["name"] for c in r.json()]
    assert "Elektro" in namen
    assert "Alt-Gewerk" not in namen  # INAKTIV bleibt aus der Auswahl


@pytest.mark.django_db
def test_project_categories_ohne_login_401(anonymous_client, db):
    r = anonymous_client.get("/api/workflow/project-categories")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_create_projekt_mit_kategorie(admin_client, app_user):
    cat = _neue_kategorie(app_user, name="Sanitär")
    r = admin_client.post(
        "/api/workflow/projects",
        data={"name": "Bad-Sanierung", "category_id": str(cat.id)},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["category"]["name"] == "Sanitär"


# --- Kontaktkarte (Hauptkontakt der ersten Liegenschaft) --------------------

@pytest.mark.django_db
def test_primary_contact_abgeleitet(admin_client, app_user):
    """Detail leitet den Hauptkontakt aus dem PROPERTY_OWNER der ersten
    Liegenschaft ab, inkl. primärer E-Mail/Telefonnummer."""
    import datetime

    from db_core.services import identity as identity_service

    obj = property_service.create_property(
        app_user.id, name="Villa Nord", property_type="EINFAMILIENHAUS",
        street="Nordweg", house_number="1", postal_code="10115", city="Berlin",
    )
    party = identity_service.create_person(
        app_user.id, first_name="Erika", last_name="Eigner"
    )
    property_service.add_party_role(
        app_user.id, property_id=obj.id, party_id=party.id,
        role="PROPERTY_OWNER", valid_from=datetime.date(2020, 1, 1),
    )
    identity_service.add_contact_point(
        app_user.id, party.id, contact_type="EMAIL",
        value="erika@example.de", is_primary=True,
    )
    identity_service.add_contact_point(
        app_user.id, party.id, contact_type="PHONE",
        value="+4930123456", is_primary=True,
    )
    proj = projekt_service.create_project(
        app_user.id, name="Dachprojekt", property_ids=[obj.id]
    )

    r = admin_client.get(f"/api/workflow/projects/{proj.id}")
    assert r.status_code == 200, r.content
    pc = r.json()["primary_contact"]
    assert pc is not None
    assert pc["display_name"] == "Erika Eigner"
    assert pc["role"] == "PROPERTY_OWNER"
    assert pc["email"] == "erika@example.de"
    assert pc["phone"] == "+4930123456"
    assert pc["property_id"] == str(obj.id)


@pytest.mark.django_db
def test_primary_contact_none_ohne_eigentuemer(admin_client, seeded):
    """Ohne PROPERTY_OWNER an einer Liegenschaft bleibt die Kontaktkarte leer."""
    r = admin_client.get(f"/api/workflow/projects/{seeded['p1'].id}")
    assert r.status_code == 200, r.content
    assert r.json()["primary_contact"] is None


# --- Ort in der Liste (Projekte-8) + Verantwortlicher (Projekte-4) ----------

@pytest.mark.django_db
def test_liste_zeigt_ort_und_verantwortlichen(admin_client, app_user):
    """Die Projektliste (ProjectOut) gibt den Ort der ersten Liegenschaft und den
    Verantwortlichen (abgeleitet über AppUser) additiv aus."""
    obj = property_service.create_property(
        app_user.id, name="Haus West", property_type="WEG",
        street="Weststr", house_number="7", postal_code="20095", city="Hamburg",
    )
    p = projekt_service.create_project(
        app_user.id, name="Ortprojekt", property_ids=[obj.id],
        responsible_user_id=app_user.id,
    )
    r = admin_client.get("/api/workflow/projects?q=Ortprojekt")
    assert r.status_code == 200, r.content
    item = next(i for i in r.json()["items"] if i["id"] == str(p.id))
    assert item["primary_city"] == "Hamburg"
    assert item["responsible_user"]["id"] == str(app_user.id)
    assert item["responsible_user"]["display_name"] == app_user.display_name


@pytest.mark.django_db
def test_liste_ohne_liegenschaft_und_ohne_verantwortlichen(admin_client, seeded):
    """p2 (Kellerentwässerung) hat weder Liegenschaft noch Verantwortlichen →
    primary_city und responsible_user bleiben null."""
    r = admin_client.get("/api/workflow/projects?q=Kellerentwässerung")
    item = next(i for i in r.json()["items"] if i["id"] == str(seeded["p2"].id))
    assert item["primary_city"] is None
    assert item["responsible_user"] is None


@pytest.mark.django_db
def test_liste_keine_n_plus_1(admin_client, app_user):
    """Die Query-Zahl der Liste ist unabhängig von der Zeilenzahl
    (select_related('category','responsible_user') + ein Prefetch der Liegenschaften)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for i in range(5):
        obj = property_service.create_property(
            app_user.id, name=f"NP-Objekt {i}", property_type="WEG",
            street="S", postal_code="1", city="Berlin",
        )
        projekt_service.create_project(
            app_user.id, name=f"NP-Projekt {i}", property_ids=[obj.id],
            responsible_user_id=app_user.id,
        )

    def _queries(url):
        with CaptureQueriesContext(connection) as ctx:
            resp = admin_client.get(url)
            assert resp.status_code == 200
        return len(ctx.captured_queries)

    q_eine = _queries("/api/workflow/projects?page_size=1")
    q_alle = _queries("/api/workflow/projects?page_size=50")
    assert q_eine == q_alle, (
        f"N+1: {q_eine} Queries bei 1 Zeile vs. {q_alle} bei vielen Zeilen"
    )


# --- Verantwortlichen zuweisen: POST .../projects/{id}/responsible ----------

@pytest.mark.django_db
def test_set_responsible_happy(admin_client, app_user):
    p = projekt_service.create_project(app_user.id, name="Zuweisung")
    r = admin_client.post(
        f"/api/workflow/projects/{p.id}/responsible",
        data={"responsible_user_id": str(app_user.id)},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["responsible_user"]["id"] == str(app_user.id)
    assert body["responsible_user"]["display_name"] == app_user.display_name


@pytest.mark.django_db
def test_set_responsible_entfernen(admin_client, app_user):
    """responsible_user_id=None entfernt die Zuweisung."""
    p = projekt_service.create_project(
        app_user.id, name="Zuweisung entfernen", responsible_user_id=app_user.id
    )
    r = admin_client.post(
        f"/api/workflow/projects/{p.id}/responsible",
        data={"responsible_user_id": None},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["responsible_user"] is None


@pytest.mark.django_db
def test_set_responsible_unbekannter_user_422(admin_client, app_user):
    p = projekt_service.create_project(app_user.id, name="Zuweisung fremd")
    r = admin_client.post(
        f"/api/workflow/projects/{p.id}/responsible",
        data={"responsible_user_id": str(uuid.uuid4())},
        content_type="application/json",
    )
    assert r.status_code == 422


@pytest.mark.django_db
def test_set_responsible_unbekanntes_projekt_404(admin_client, app_user):
    r = admin_client.post(
        f"/api/workflow/projects/{uuid.uuid4()}/responsible",
        data={"responsible_user_id": str(app_user.id)},
        content_type="application/json",
    )
    assert r.status_code == 404


@pytest.mark.django_db
def test_set_responsible_monteur_403_fail_closed(client_with_role, seeded):
    """set_project_responsible nutzt `require` (AENDERN): der Monteur hat nur Scope
    'EIGENE', der Endpunkt wertet ihn nicht aus → fail-closed 403."""
    c = client_with_role("MONTEUR")
    r = c.post(
        f"/api/workflow/projects/{seeded['p1'].id}/responsible",
        data={"responsible_user_id": str(seeded['app_user'].id)},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_set_responsible_ohne_login_401(anonymous_client, seeded):
    r = anonymous_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/responsible",
        data={"responsible_user_id": str(seeded['app_user'].id)},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


# --- Freies Notizfeld am Projekt (Hero-Angleichung Projekte-7) --------------

@pytest.mark.django_db
def test_internal_note_setzen_und_auslesen(admin_client, seeded):
    # Frisch angelegtes Projekt hat kein Notizfeld.
    r0 = admin_client.get(f"/api/workflow/projects/{seeded['p1'].id}")
    assert r0.status_code == 200
    assert r0.json()["internal_note"] is None

    r = admin_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/internal-note",
        data={"internal_note": "  Kunde ruft freitags zurueck  "},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    # Antwort trägt die (getrimmte) Notiz sofort.
    assert r.json()["internal_note"] == "Kunde ruft freitags zurueck"
    # Und sie ist persistent auslesbar.
    r2 = admin_client.get(f"/api/workflow/projects/{seeded['p1'].id}")
    assert r2.json()["internal_note"] == "Kunde ruft freitags zurueck"

    # Leeren normalisiert auf None.
    r3 = admin_client.post(
        f"/api/workflow/projects/{seeded['p1'].id}/internal-note",
        data={"internal_note": "   "},
        content_type="application/json",
    )
    assert r3.status_code == 200
    assert r3.json()["internal_note"] is None


# --- Belege bei der Aufstufung mitziehen (Migration 0113) -------------------

@pytest.mark.django_db
def test_promote_haengt_belege_um(admin_client, app_user):
    """Hochstufen zieht Belege am Vorgang UND an dessen Aufträgen ins Projekt."""
    from db_core.models import Invoice, Quote
    from db_core.services import auftrag as auftrag_service
    from db_core.services import beleg as beleg_service

    obj = property_service.create_property(
        app_user.id, name="EFH", property_type="EINFAMILIENHAUS",
        street="Weg", postal_code="10115", city="Berlin",
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Sanierung",
    )
    wo = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag", service_case_id=case.id,
    )
    line = {"line_type": "MATERIAL", "description": "P", "quantity": 1,
            "unit_price": 100, "tax_code": "DE_19"}
    # Angebot direkt am Vorgang, Rechnung über den Auftrag (erbt den Vorgang).
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="A", service_case_id=case.id, lines=[line],
    )
    invoice = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, work_order_id=wo.id, lines=[line],
    )
    assert quote.project_id is None and invoice.project_id is None

    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/promote-to-project",
        data={}, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    proj_id = r.json()["id"]
    assert str(Quote.objects.get(id=quote.id).project_id) == proj_id
    assert str(Invoice.objects.get(id=invoice.id).project_id) == proj_id


@pytest.mark.django_db
def test_promote_stiehlt_keine_fremden_belege(admin_client, app_user):
    """Belege eines anderen Projekts bleiben bei der Aufstufung unberührt."""
    from db_core.models import Quote
    from db_core.services import beleg as beleg_service

    obj = property_service.create_property(
        app_user.id, name="EFH", property_type="EINFAMILIENHAUS",
        street="Weg", postal_code="10115", city="Berlin",
    )
    fremd = projekt_service.create_project(
        app_user.id, name="Fremdprojekt", property_ids=[obj.id],
    )
    line = {"line_type": "MATERIAL", "description": "P", "quantity": 1,
            "unit_price": 100, "tax_code": "DE_19"}
    quote_fremd = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Fremd", project_id=fremd.id, lines=[line],
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Neu",
    )
    quote_mein = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Mein", service_case_id=case.id, lines=[line],
    )
    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/promote-to-project",
        data={}, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    proj_id = r.json()["id"]
    assert str(Quote.objects.get(id=quote_mein.id).project_id) == proj_id
    # Das fremde Angebot behält sein Projekt.
    assert str(Quote.objects.get(id=quote_fremd.id).project_id) == str(fremd.id)


# --- Liegenschaftsfilter (Objekt-Detailansicht) -----------------------------

@pytest.mark.django_db
def test_projects_filter_property_id(admin_client, app_user):
    """GET /projects?property_id liefert nur Projekte dieser Liegenschaft."""
    obj_a = property_service.create_property(
        app_user.id, name="A", property_type="WEG",
        street="A", postal_code="10115", city="Berlin",
    )
    obj_b = property_service.create_property(
        app_user.id, name="B", property_type="WEG",
        street="B", postal_code="10115", city="Berlin",
    )
    p_a = projekt_service.create_project(app_user.id, name="PA", property_ids=[obj_a.id])
    projekt_service.create_project(app_user.id, name="PB", property_ids=[obj_b.id])
    r = admin_client.get(f"/api/workflow/projects?property_id={obj_a.id}")
    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {str(p_a.id)}


@pytest.mark.django_db
def test_service_cases_filter_property_id(admin_client, app_user):
    """GET /service_cases?property_id liefert nur Vorgänge dieser Liegenschaft."""
    obj_a = property_service.create_property(
        app_user.id, name="A", property_type="WEG",
        street="A", postal_code="10115", city="Berlin",
    )
    obj_b = property_service.create_property(
        app_user.id, name="B", property_type="WEG",
        street="B", postal_code="10115", city="Berlin",
    )
    case_a = projekt_service.create_service_case(
        app_user.id, property_id=obj_a.id, subject="CA",
    )
    projekt_service.create_service_case(
        app_user.id, property_id=obj_b.id, subject="CB",
    )
    r = admin_client.get(f"/api/workflow/service_cases?property_id={obj_a.id}")
    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {str(case_a.id)}


@pytest.mark.django_db
def test_promote_laesst_versendete_belege_unberuehrt(admin_client, app_user):
    """Ein versendetes (eingefrorenes) Angebot bricht die Aufstufung nicht ab und
    bleibt projektlos (B-30: project_id ist nach Versand eingefroren)."""
    from db_core.models import Quote
    from db_core.services import auftrag as auftrag_service
    from db_core.services import beleg as beleg_service

    obj = property_service.create_property(
        app_user.id, name="EFH", property_type="EINFAMILIENHAUS",
        street="Weg", postal_code="10115", city="Berlin",
    )
    case = projekt_service.create_service_case(
        app_user.id, property_id=obj.id, subject="Gewachsen",
    )
    wo = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag", service_case_id=case.id,
    )
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="A", work_order_id=wo.id,
        lines=[{"line_type": "MATERIAL", "description": "P", "quantity": 1,
                "unit_price": 100, "tax_code": "DE_19"}],
    )
    beleg_service.send_quote(app_user.id, quote_id=quote.id)

    r = admin_client.post(
        f"/api/workflow/service_cases/{case.id}/promote-to-project",
        data={}, content_type="application/json",
    )
    assert r.status_code == 201, r.content
    # Das versendete Angebot bleibt projektlos (kein Trigger-Abbruch).
    assert Quote.objects.get(id=quote.id).project_id is None
