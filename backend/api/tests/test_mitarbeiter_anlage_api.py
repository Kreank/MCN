"""Mitarbeiter anlegen ohne Umweg über den Kundenstamm (Befund F1, Runde 2).

Sascha: „Neuer Mitarbeiter anlegen. Warum kann ich Personen aus meinen Kontakten
da finden? Ich lege meine Mitarbeiter ja nicht wie einen Kunden an!"

Das Datenmodell war nicht das Problem — `hr.employee` ist ein eigenes Schema mit
Personalnummer, Verträgen und Abwesenheiten; Lohn läuft über die Lohngruppe,
Zeiterfassung über das Login-Konto. Die Party trägt für Mitarbeiter faktisch nur
Vor- und Nachname.

Das Problem war der Weg dorthin: Der Anlage-Dialog hatte **kein einziges
Namensfeld** und zwang zur Auswahl aus derselben Trefferliste, in der Kunden,
Mieter und Verwalter stehen. Datenschutzrechtlich die falsche Richtung —
Beschäftigten- und Kundendaten haben verschiedene Rechtsgrundlagen, Zwecke und
Löschfristen.
"""
import uuid
from datetime import date

import pytest

from db_core.models import AppUser, Employee, Person
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service


def _konto(name="Konto"):
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=name, status="ACTIVE", version=1
    )


@pytest.mark.django_db
def test_mitarbeiter_mit_namen_anlegen(admin_client):
    """Der Kern: Namensfelder statt Kontakt-Picker."""
    konto = _konto("Monteur-Konto")
    r = admin_client.post(
        "/api/hr/employees",
        data={
            "app_user_id": str(konto.id),
            "first_name": "Jonas",
            "last_name": "Berger",
            "hired_on": "2026-02-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["first_name"] == "Jonas"
    assert body["last_name"] == "Berger"
    assert body["display_name"] == "Jonas Berger"
    assert body["employee_number"].startswith("MA-")

    # Die Person entstand im Hintergrund — der Anwender hat sie nie gewählt.
    assert Person.objects.filter(last_name="Berger").exists()


@pytest.mark.django_db
def test_ohne_vornamen_geht_auch(admin_client):
    """Seit Migration 0125 ist der Vorname optional — das gilt hier auch."""
    konto = _konto("Konto ohne Vornamen")
    r = admin_client.post(
        "/api/hr/employees",
        data={
            "app_user_id": str(konto.id),
            "last_name": "Özdemir",
            "hired_on": "2026-02-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["display_name"] == "Özdemir"


@pytest.mark.django_db
def test_ohne_nachnamen_und_ohne_person_ist_422(admin_client):
    konto = _konto("Konto ohne Namen")
    r = admin_client.post(
        "/api/hr/employees",
        data={"app_user_id": str(konto.id), "hired_on": "2026-02-01"},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "Nachname" in r.json()["detail"]


@pytest.mark.django_db
def test_bestehende_person_bleibt_moeglich(admin_client, app_user):
    """Der Monteur, der zugleich Kunde ist — im Handwerk nicht selten.

    Odoo macht es genauso: die Kontakt-Verknüpfung ist optional, nicht
    verboten.
    """
    person = identity_service.create_person(
        app_user.id, first_name="Sven", last_name="Kunde-und-Monteur"
    )
    konto = _konto("Doppelrolle")
    r = admin_client.post(
        "/api/hr/employees",
        data={
            "app_user_id": str(konto.id),
            "party_id": str(person.id),
            "hired_on": "2026-02-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert Employee.objects.get(id=r.json()["id"]).party_id == person.id


@pytest.mark.django_db
def test_scheitert_der_personalsatz_bleibt_keine_person_zurueck(admin_client, app_user):
    """Alles oder nichts — eine Person-Waise wäre wegen des No-Delete-Schutzes
    nicht mehr zu entfernen."""
    person = identity_service.create_person(
        app_user.id, first_name="Schon", last_name="Angestellt"
    )
    erstes_konto = _konto("Erstes")
    mitarbeiter_service.create_employee(
        app_user.id,
        app_user_id=erstes_konto.id,
        party_id=person.id,
        hired_on=date(2026, 1, 1),
    )

    vorher = Person.objects.count()
    # Dasselbe Konto ein zweites Mal → der Personalsatz scheitert, NACHDEM die
    # neue Person angelegt worden wäre.
    r = admin_client.post(
        "/api/hr/employees",
        data={
            "app_user_id": str(erstes_konto.id),
            "first_name": "Waise",
            "last_name": "Bleibtnicht",
            "hired_on": "2026-02-01",
        },
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert Person.objects.count() == vorher
    assert not Person.objects.filter(last_name="Bleibtnicht").exists()


# --- Mitarbeiter aus der Kontaktliste heraushalten --------------------------


@pytest.mark.django_db
def test_mitarbeiter_lassen_sich_aus_der_kontaktliste_filtern(admin_client, app_user):
    """`identity.party` kennt keinen Rollen-Diskriminator — eine
    Mitarbeiter-Person ist von einem Kunden nicht zu unterscheiden.

    Deshalb der Filter über die Rückbeziehung, nicht über ein neues Feld an der
    Party: Beschäftigung ist eine Rolle, keine Eigenschaft des Kontakts.
    """
    identity_service.create_person(app_user.id, first_name="Klara", last_name="Kundin")
    person = identity_service.create_person(
        app_user.id, first_name="Mario", last_name="Monteur"
    )
    mitarbeiter_service.create_employee(
        app_user.id,
        app_user_id=_konto("Mario-Konto").id,
        party_id=person.id,
        hired_on=date(2026, 1, 1),
    )

    alle = admin_client.get("/api/identity/parties?page_size=100").json()["items"]
    namen_alle = {p["display_name"] for p in alle}
    assert "Klara Kundin" in namen_alle
    assert "Mario Monteur" in namen_alle

    ohne = admin_client.get(
        "/api/identity/parties?page_size=100&mitarbeiter_zeigen=false"
    ).json()["items"]
    namen_ohne = {p["display_name"] for p in ohne}
    assert "Klara Kundin" in namen_ohne
    assert "Mario Monteur" not in namen_ohne


@pytest.mark.django_db
def test_organisationen_bleiben_beim_filtern_erhalten(admin_client, app_user):
    """Der Filter geht über `person__employee` — eine Organisation hat gar
    keine `person`-Zeile und dürfte dadurch nicht herausfallen."""
    identity_service.create_organization(
        app_user.id, legal_name="Zulieferer GmbH", organization_type="COMPANY"
    )
    ohne = admin_client.get(
        "/api/identity/parties?page_size=100&mitarbeiter_zeigen=false"
    ).json()["items"]
    assert "Zulieferer GmbH" in {p["display_name"] for p in ohne}
