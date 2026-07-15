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
def test_quick_intake_leerer_name_422_statt_500(admin_client, db):
    """Nur-Leerzeichen-Name ist ein Fachfehler → 422 (nicht 500), und der erste
    Schritt scheitert vor jeder Anlage: keine Waisen."""
    from db_core.models import Party

    parties_vorher = Party.objects.count()
    payload = _quick_intake_payload(
        person={"first_name": "   ", "last_name": "Mustermann"}
    )
    r = admin_client.post(
        "/api/workflow/quick-intake", data=payload, content_type="application/json"
    )
    assert r.status_code == 422, r.content
    assert Party.objects.count() == parties_vorher


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
