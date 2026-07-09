"""API-Tests der Personal-Endpoints (hr.*) über den Django-Test-Client.

Alle Endpunkte verlangen Anmeldung und das Recht auf dem Modul `hr`; die Tests
nutzen dafür den `admin_client`. Der Zustand wird ausschließlich über die
Service-Schicht aufgebaut (echte Trigger/Constraints), nicht über rohe
ORM-Creates.
"""
import uuid
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from db_core.models import AppUser

User = get_user_model()
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service

# Vollzeit Mo–Fr, 8 Stunden → 40 Wochenstunden, Wochenende frei.
VOLLZEIT = {
    "hours_monday": 8,
    "hours_tuesday": 8,
    "hours_wednesday": 8,
    "hours_thursday": 8,
    "hours_friday": 8,
}
# Teilzeit Mo/Di, 8 Stunden → 16 Wochenstunden.
TEILZEIT = {"hours_monday": 8, "hours_tuesday": 8}


def _account(display_name="Personal-Konto"):
    """Ein eigenes security.app_user als Login-Konto des Mitarbeiters."""
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=display_name, status="ACTIVE", version=1
    )


def _employee(actor, *, first_name, last_name, hired_on=date(2024, 1, 1)):
    person = identity_service.create_person(actor.id, first_name, last_name)
    return mitarbeiter_service.create_employee(
        actor.id,
        app_user_id=_account(f"Konto {first_name}").id,
        party_id=person.id,
        hired_on=hired_on,
    )


@pytest.fixture
def seeded(app_user):
    """Drei Mitarbeiter (AKTIV/INAKTIV/AUSGETRETEN) mit Verträgen, Abwesenheiten
    und Urlaubskonto. Der Ausgetretene trägt einen alphabetisch früheren
    Nachnamen als der Aktive, damit die Statussortierung prüfbar wird.
    """
    actor = app_user

    # AKTIV — Nachname "Muster" (alphabetisch spät), Vollzeitvertrag.
    aktiv = _employee(actor, first_name="Anna", last_name="Muster")
    mitarbeiter_service.create_contract(
        actor.id,
        employee_id=aktiv.id,
        valid_from=date(2024, 1, 1),
        hours=VOLLZEIT,
        vacation_days_per_year=Decimal("30"),
    )
    mitarbeiter_service.set_vacation_budget(
        actor.id, employee_id=aktiv.id, year=2024, entitlement_days=Decimal("30")
    )
    # Genehmigter Urlaub (Mo–Fr, 5 Arbeitstage).
    genehmigt = mitarbeiter_service.create_absence(
        actor.id,
        employee_id=aktiv.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 7),
    )
    mitarbeiter_service.submit_absence(actor.id, absence_id=genehmigt.id)
    mitarbeiter_service.approve_absence(actor.id, absence_id=genehmigt.id)
    # Eingereichter Urlaub (Mo–Mi), noch offen (Genehmigungs-Eingang).
    eingereicht = mitarbeiter_service.create_absence(
        actor.id,
        employee_id=aktiv.id,
        absence_type="URLAUB",
        start_date=date(2024, 7, 1),
        end_date=date(2024, 7, 3),
    )
    mitarbeiter_service.submit_absence(actor.id, absence_id=eingereicht.id)

    # INAKTIV — Teilzeitvertrag.
    inaktiv = _employee(actor, first_name="Carla", last_name="Nowak")
    mitarbeiter_service.create_contract(
        actor.id,
        employee_id=inaktiv.id,
        valid_from=date(2024, 1, 1),
        hours=TEILZEIT,
        vacation_days_per_year=Decimal("12"),
    )
    mitarbeiter_service.set_employee_status(
        actor.id, employee_id=inaktiv.id, status="INAKTIV"
    )

    # AUSGETRETEN — Nachname "Abele" (alphabetisch früh). Vertrag VOR dem
    # Austritt anlegen (danach lehnt der Service neue Verträge ab).
    ausgetreten = _employee(actor, first_name="Bernd", last_name="Abele")
    mitarbeiter_service.create_contract(
        actor.id,
        employee_id=ausgetreten.id,
        valid_from=date(2024, 1, 1),
        hours=VOLLZEIT,
        vacation_days_per_year=Decimal("30"),
    )
    mitarbeiter_service.set_employee_status(
        actor.id,
        employee_id=ausgetreten.id,
        status="AUSGETRETEN",
        left_on=date(2024, 12, 31),
    )

    return {
        "aktiv": aktiv,
        "inaktiv": inaktiv,
        "ausgetreten": ausgetreten,
        "genehmigt": genehmigt,
        "eingereicht": eingereicht,
    }


def _logged_in_client(client, *, with_app_user=True):
    """Ein eingeloggter Django-User, optional mit zugeordnetem app_user."""
    user = User.objects.create_user(
        username=f"u{uuid.uuid4().hex[:8]}", password="x"
    )
    if with_app_user:
        from .conftest import grant_role
        au = AppUser.objects.create(
            id=uuid.uuid4(), display_name="Login-Akteur", status="ACTIVE", version=1
        )
        user.app_user_id = au.id
        user.save()
        grant_role(au.id, "ADMINISTRATION")
    client.force_login(user)
    return client


# --- GET /api/hr/employees -------------------------------------------------


@pytest.mark.django_db
def test_liste_und_pagination_felder(admin_client, seeded):
    r = admin_client.get("/api/hr/employees")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 25
    assert len(body["items"]) == 3
    it = body["items"][0]
    assert it["employee_number"].startswith("MA-")
    assert "display_name" in it
    assert "status" in it


@pytest.mark.django_db
def test_suche_nach_name(admin_client, seeded):
    r = admin_client.get("/api/hr/employees?q=Muster")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["last_name"] == "Muster"


@pytest.mark.django_db
def test_suche_nach_personalnummer(admin_client, seeded):
    nummer = seeded["aktiv"].employee_number
    r = admin_client.get(f"/api/hr/employees?q={nummer}")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["employee_number"] == nummer


@pytest.mark.django_db
def test_statusfilter(admin_client, seeded):
    assert admin_client.get("/api/hr/employees?status=AKTIV").json()["total"] == 1
    assert admin_client.get("/api/hr/employees?status=INAKTIV").json()["total"] == 1
    assert admin_client.get("/api/hr/employees?status=AUSGETRETEN").json()["total"] == 1
    aus = admin_client.get("/api/hr/employees?status=AUSGETRETEN").json()
    assert aus["items"][0]["last_name"] == "Abele"


@pytest.mark.django_db
def test_unbekannter_status_422(admin_client, seeded):
    r = admin_client.get("/api/hr/employees?status=QUATSCH")
    assert r.status_code == 422


@pytest.mark.django_db
def test_sortierung_aktiv_vor_ausgetreten(admin_client, seeded):
    """AKTIV (rank 0) steht vor AUSGETRETEN (rank 2), obwohl der Ausgetretene
    ('Abele') alphabetisch vor dem Aktiven ('Muster') läge."""
    body = admin_client.get("/api/hr/employees").json()
    stati = [i["status"] for i in body["items"]]
    assert stati.index("AKTIV") < stati.index("AUSGETRETEN")
    # der Ausgetretene ist der Letzte in der Liste
    assert body["items"][-1]["status"] == "AUSGETRETEN"


# --- GET /api/hr/employees/{id} --------------------------------------------


@pytest.mark.django_db
def test_detail_mit_mappe(admin_client, seeded):
    emp = seeded["aktiv"]
    r = admin_client.get(f"/api/hr/employees/{emp.id}?year=2024")
    assert r.status_code == 200
    body = r.json()
    assert body["first_name"] == "Anna"
    assert body["employee_number"] == emp.employee_number

    # Vertrag: laufend, 40 Wochenstunden.
    assert len(body["contracts"]) == 1
    contract = body["contracts"][0]
    assert contract["is_current"] is True
    assert contract["weekly_hours"] == "40.00"

    # Abwesenheiten: genehmigt (5 Tage) + eingereicht (3 Tage).
    assert len(body["absences"]) == 2
    stati = {a["status"] for a in body["absences"]}
    assert stati == {"GENEHMIGT", "EINGEREICHT"}

    # Urlaubskonto 2024: 30 Anspruch, 5 genehmigt verbraucht, 25 Rest.
    konto = body["vacation_account"]
    assert konto["year"] == 2024
    assert konto["total_days"] == "30.00"
    assert konto["used_days"] == "5.00"
    assert konto["remaining_days"] == "25.00"


@pytest.mark.django_db
def test_detail_404(admin_client, seeded):
    r = admin_client.get(f"/api/hr/employees/{uuid4()}")
    assert r.status_code == 404


# --- GET /api/hr/absences --------------------------------------------------
# Dieser Endpunkt verlangt als einziger Lese-Endpunkt eine Session: er gibt
# Krankheitsdaten über den gesamten Personalbestand aus (DSGVO Art. 9).


@pytest.mark.django_db
def test_absences_ohne_login_401(anonymous_client, seeded):
    r = anonymous_client.get("/api/hr/absences")
    assert r.status_code == 401


@pytest.mark.django_db
def test_absences_statusfilter(client, seeded):
    client = _logged_in_client(client)
    genehmigt = client.get("/api/hr/absences?status=GENEHMIGT").json()
    assert len(genehmigt) == 1
    assert genehmigt[0]["id"] == str(seeded["genehmigt"].id)
    assert genehmigt[0]["days_count"] == "5.00"

    eingereicht = client.get("/api/hr/absences?status=EINGEREICHT").json()
    assert len(eingereicht) == 1
    assert eingereicht[0]["id"] == str(seeded["eingereicht"].id)


@pytest.mark.django_db
def test_absences_unbekannter_status_422(client, seeded):
    client = _logged_in_client(client)
    r = client.get("/api/hr/absences?status=QUATSCH")
    assert r.status_code == 422


# --- Schreib-Endpunkte: Auth-Tore ------------------------------------------


@pytest.mark.django_db
def test_create_employee_ohne_login_401(anonymous_client, db):
    r = anonymous_client.post(
        "/api/hr/employees",
        data={
            "app_user_id": str(uuid4()),
            "party_id": str(uuid4()),
            "hired_on": "2024-01-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 401


@pytest.mark.django_db
def test_approve_absence_ohne_login_401(anonymous_client, seeded):
    r = anonymous_client.post(
        f"/api/hr/absences/{seeded['eingereicht'].id}/approve",
        data={"note": "ok"},
        content_type="application/json",
    )
    assert r.status_code == 401


@pytest.mark.django_db
def test_create_employee_eingeloggt_ohne_app_user_403(client, db):
    """Login ohne zugeordnetes app_user → fachliche Writes sind gesperrt (403)."""
    c = _logged_in_client(client, with_app_user=False)
    r = c.post(
        "/api/hr/employees",
        data={
            "app_user_id": str(uuid4()),
            "party_id": str(uuid4()),
            "hired_on": "2024-01-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 403
