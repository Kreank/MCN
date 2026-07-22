"""Der Mitarbeiter beantragt seinen Urlaub selbst (Befund E6, Migration 0130).

Sascha: „Unter diesem Reiter sollten wir auch die Möglichkeit einbauen:
Urlaubsanträge, Krankheit-Anträge, Überstundenausgleich beantragen usw."

Es fehlte **nicht** die Oberfläche. Der MONTEUR lief an drei Toren auf:
`hr/ANLEGEN` stand für ihn auf `false`, Einreichen und Zurückziehen liefen über
`require(...)`, das row_scope EIGENE hart abweist, und Genehmigen verlangt
`hr/FREIGEBEN`.

Das dritte Tor bleibt — **wer beantragt, entscheidet nicht über sich selbst.**
Diese Tests halten genau diese Trennlinie fest.
"""
from datetime import date

import pytest
from django.test import Client

from db_core.services import mitarbeiter as mitarbeiter_service

from .conftest import make_app_user, make_role_user

# Ein Montag und der Folgetag — im Sollstunden-Raster garantiert Arbeitstage,
# sonst rechnet der Service 0 Tage und der DB-CHECK (days_count > 0) greift.
VON = date(2026, 8, 3)
BIS = date(2026, 8, 4)

VOLLZEIT = {
    "hours_monday": 8, "hours_tuesday": 8, "hours_wednesday": 8,
    "hours_thursday": 8, "hours_friday": 8,
}


def _personalsatz(actor_id, konto_id, vorname, nachname, *, mit_vertrag=True):
    employee = mitarbeiter_service.create_employee(
        actor_id,
        app_user_id=konto_id,
        first_name=vorname,
        last_name=nachname,
        hired_on=date(2025, 1, 1),
    )
    if mit_vertrag:
        mitarbeiter_service.create_contract(
            actor_id,
            employee_id=employee.id,
            valid_from=date(2025, 1, 1),
            hours=VOLLZEIT,
            vacation_days_per_year=30,
        )
    return employee


@pytest.fixture
def buero(db):
    """Der Akteur der Personalverwaltung — legt die Stammdaten der Tests an."""
    return make_app_user("Personalverwaltung")


@pytest.fixture
def monteur(db, buero):
    """Ein MONTEUR mit eigenem Personalsatz, Vertrag und eingeloggtem Client."""
    user, konto = make_role_user("MONTEUR")
    employee = _personalsatz(buero.id, konto.id, "Mario", "Monteur")
    client = Client()
    client.force_login(user)
    return {"client": client, "employee": employee, "konto": konto}


def _antrag_stellen(client, employee_id, art="URLAUB"):
    return client.post(
        f"/api/hr/employees/{employee_id}/absences",
        data={
            "absence_type": art,
            "start_date": VON.isoformat(),
            "end_date": BIS.isoformat(),
        },
        content_type="application/json",
    )


@pytest.mark.django_db
def test_monteur_beantragt_eigenen_urlaub(monteur):
    """Das Tor, das vorher `hr/ANLEGEN = false` verschlossen hat."""
    r = _antrag_stellen(monteur["client"], monteur["employee"].id)
    assert r.status_code == 201, r.content
    assert r.json()["status"] == "ENTWURF"


@pytest.mark.django_db
def test_monteur_reicht_seinen_antrag_ein(monteur):
    """Einreichen lief über `require(...)` und endete für EIGENE mit 403."""
    r = _antrag_stellen(monteur["client"], monteur["employee"].id, art="KRANKHEIT")
    assert r.status_code == 201, r.content

    r = monteur["client"].post(
        f"/api/hr/absences/{r.json()['id']}/submit", content_type="application/json"
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "EINGEREICHT"


@pytest.mark.django_db
def test_monteur_zieht_seinen_antrag_zurueck(monteur):
    absence_id = _antrag_stellen(monteur["client"], monteur["employee"].id).json()["id"]

    r = monteur["client"].post(
        f"/api/hr/absences/{absence_id}/withdraw", content_type="application/json"
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "ZURUECKGEZOGEN"


@pytest.mark.django_db
def test_monteur_genehmigt_seinen_antrag_NICHT(monteur):
    """Die Trennlinie, um die es geht: Wer beantragt, entscheidet nicht.

    Genehmigen verlangt `hr/FREIGEBEN` — das trägt der MONTEUR nicht, und
    daran ändert Migration 0130 nichts.
    """
    absence_id = _antrag_stellen(monteur["client"], monteur["employee"].id).json()["id"]
    monteur["client"].post(
        f"/api/hr/absences/{absence_id}/submit", content_type="application/json"
    )

    r = monteur["client"].post(
        f"/api/hr/absences/{absence_id}/approve",
        data={"note": "Passt schon"},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_monteur_beantragt_nichts_fuer_andere(monteur, buero):
    """Die Objektgrenze: 404, nicht 403 — eine 403 verriete die Existenz."""
    fremdes_konto = make_app_user("Fremde Kollegin")
    fremder = _personalsatz(
        buero.id, fremdes_konto.id, "Fremde", "Kollegin", mit_vertrag=False
    )

    r = _antrag_stellen(monteur["client"], fremder.id)
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_monteur_reicht_fremde_antraege_nicht_ein(monteur, buero):
    """Auch der Statuswechsel prüft den Besitz, nicht nur die Anlage."""
    fremdes_konto = make_app_user("Fremder Kollege")
    fremder = _personalsatz(buero.id, fremdes_konto.id, "Fremder", "Kollege")
    fremde_absence = mitarbeiter_service.create_absence(
        buero.id,
        employee_id=fremder.id,
        absence_type="URLAUB",
        start_date=VON,
        end_date=BIS,
    )

    r = monteur["client"].post(
        f"/api/hr/absences/{fremde_absence.id}/submit",
        content_type="application/json",
    )
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_neues_anlegen_recht_oeffnet_sonst_nichts(monteur, buero):
    """Der Regressionstest zur eigentlichen Sorge hinter Migration 0130.

    `hr/ANLEGEN` mit Scope EIGENE ist die **Voraussetzung** für den eigenen
    Abwesenheitsantrag, nicht die Erlaubnis für alles im Modul. Was das Recht
    nicht öffnet, halten die übrigen Endpunkte über `require(...)` zu, das Scope
    EIGENE ausnahmslos abweist.

    Ohne diesen Test kippte ein späterer Wechsel eines dieser Endpunkte auf
    `require_scoped` unbemerkt ein Loch auf — der Monteur legte sich seinen
    eigenen Arbeitsvertrag an.
    """
    c = monteur["client"]

    # Personalsatz anlegen — Sache der Personalverwaltung.
    r = c.post(
        "/api/hr/employees",
        data={"app_user_id": str(monteur["konto"].id), "hired_on": "2025-01-01"},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content

    # Arbeitsvertrag anlegen — erst recht.
    r = c.post(
        f"/api/hr/employees/{monteur['employee'].id}/contracts",
        data={
            "valid_from": "2025-01-01",
            "vacation_days_per_year": "30",
            "hours_monday": "8",
        },
        content_type="application/json",
    )
    assert r.status_code == 403, r.content

    # Zeitkategorien sind Stammdaten des Betriebs.
    r = c.post(
        "/api/hr/zeitkategorien",
        data={"code": "SCHWARZARBEIT", "name": "Schwarzarbeit", "is_work_time": True},
        content_type="application/json",
    )
    assert r.status_code == 403, r.content


@pytest.mark.django_db
def test_verwaltung_kann_weiterhin_fuer_alle(admin_client, buero):
    """Die Personalverwaltung (Scope ALLE) bleibt unverändert handlungsfähig."""
    konto = make_app_user("Verwaltete Person")
    employee = _personalsatz(buero.id, konto.id, "Verwaltet", "Person")

    r = _antrag_stellen(admin_client, employee.id)
    assert r.status_code == 201, r.content
