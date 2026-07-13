"""API-Tests: Qualifikationen, Bedarf, Nachweise, Zuweisungs-Vorlagen (0078).

Die wichtigste Prüfung hier ist der **Rechte-Zuschnitt**: Der Katalog und der
Bedarf sind Planungsstammdaten (`workflow`), der NACHWEIS am Mitarbeiter ist ein
Personaldatum (`hr`). Ein Disponent ohne `hr`-Recht sieht auf der Plantafel die
FOLGE („X hat keinen Nachweis für Gasschein"), aber nicht die Akte.
"""
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest

from db_core.models import AppUser
from db_core.services import auftrag as auftrag_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as hr_service
from db_core.services import planung as planung_service
from db_core.services import property as property_service
from db_core.services import qualifikation as q_service

T0 = datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def szenario(app_user):
    person = identity_service.create_person(
        app_user.id, first_name="Timo", last_name="Kalinski"
    )
    login = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Timo Kalinski", status="ACTIVE", version=1
    )
    emp = hr_service.create_employee(
        app_user.id, app_user_id=login.id, party_id=person.id,
        hired_on=date(2024, 1, 1),
    )
    kat = planung_service.create_category(app_user.id, name="Wartung Gastherme")
    obj = property_service.create_property(
        app_user.id, name="Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Wartung"
    )
    job = planung_service.create_termin(
        app_user.id, work_order_id=order.id,
        scheduled_start=T0, scheduled_end=T0 + timedelta(hours=2),
        appointment_category_id=kat.id, assignee_ids=[login.id],
    )
    return {"employee": emp, "login": login, "kat": kat, "job": job}


@pytest.mark.django_db
def test_katalog_anlegen_mit_freier_art(admin_client):
    """`kind` ist ein freier Datenwert — eine neue Art kostet keinen Deploy."""
    r = admin_client.post(
        "/api/planung/qualifikationen",
        {"code": "PSAgA", "label": "Absturzsicherung", "kind": "SICHERHEIT",
         "expires": True},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["kind"] == "SICHERHEIT"
    assert r.json()["expires"] is True


@pytest.mark.django_db
def test_bedarf_an_kategorie_und_termin_wirkt_vereinigt(admin_client, szenario):
    gas = admin_client.post(
        "/api/planung/qualifikationen",
        {"code": "G", "label": "Gasschein"},
        content_type="application/json",
    ).json()
    psa = admin_client.post(
        "/api/planung/qualifikationen",
        {"code": "P", "label": "Absturzsicherung"},
        content_type="application/json",
    ).json()

    r = admin_client.put(
        f"/api/planung/kategorien/{szenario['kat'].id}/qualifikationen",
        {"qualification_ids": [gas["id"]]},
        content_type="application/json",
    )
    assert r.status_code == 200
    r = admin_client.put(
        f"/api/planung/einsaetze/{szenario['job'].id}/qualifikationen",
        {"qualification_ids": [psa["id"]]},
        content_type="application/json",
    )
    assert r.status_code == 200

    # Die Plantafel meldet BEIDE als weichen Konflikt — und blockiert nichts.
    r = admin_client.get(
        f"/api/planung/plantafel?date_from={T0:%Y-%m-%d}&date_to={T0:%Y-%m-%d}"
    )
    assert r.status_code == 200
    kachel = next(j for j in r.json()["jobs"] if j["id"] == str(szenario["job"].id))
    texte = [k["text"] for k in kachel["conflicts"] if k["kind"] == "QUALIFIKATION"]
    assert len(texte) == 2
    assert any("Gasschein" in t for t in texte)
    assert any("Absturzsicherung" in t for t in texte)


@pytest.mark.django_db
def test_nachweis_eintragen_macht_die_warnung_weg(admin_client, szenario):
    gas = admin_client.post(
        "/api/planung/qualifikationen",
        {"code": "G", "label": "Gasschein", "expires": True},
        content_type="application/json",
    ).json()
    admin_client.put(
        f"/api/planung/kategorien/{szenario['kat'].id}/qualifikationen",
        {"qualification_ids": [gas["id"]]},
        content_type="application/json",
    )

    r = admin_client.put(
        f"/api/planung/mitarbeiter/{szenario['employee'].id}/qualifikationen",
        {"qualification_id": gas["id"], "valid_until": "2029-12-31"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["gueltig_heute"] is True

    r = admin_client.get(
        f"/api/planung/plantafel?date_from={T0:%Y-%m-%d}&date_to={T0:%Y-%m-%d}"
    )
    kachel = next(j for j in r.json()["jobs"] if j["id"] == str(szenario["job"].id))
    assert not [k for k in kachel["conflicts"] if k["kind"] == "QUALIFIKATION"]


@pytest.mark.django_db
def test_ablaufpflicht_ohne_gueltig_bis_ist_422(admin_client, szenario):
    gas = admin_client.post(
        "/api/planung/qualifikationen",
        {"code": "G", "label": "Gasschein", "expires": True},
        content_type="application/json",
    ).json()
    r = admin_client.put(
        f"/api/planung/mitarbeiter/{szenario['employee'].id}/qualifikationen",
        {"qualification_id": gas["id"]},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert "ablaufpflichtig" in r.json()["detail"]


# --- Die Rechte-Grenze: Folge ja, Akte nein ---------------------------------

@pytest.mark.django_db
def test_disposition_sieht_die_folge_aber_nicht_die_akte(client_with_role, szenario,
                                                         app_user):
    """DISPOSITION hat `workflow`, aber kein `hr`. Sie muss auf dem Board sehen,
    DASS ein Nachweis fehlt — und darf trotzdem nicht in die Personalakte."""
    gas = q_service.create_qualification(app_user.id, code="G", label="Gasschein")
    q_service.set_category_qualifications(
        app_user.id, category_id=szenario["kat"].id, qualification_ids=[gas.id]
    )
    c = client_with_role("DISPOSITION")

    # Die FOLGE steht auf dem Board.
    r = c.get(f"/api/planung/plantafel?date_from={T0:%Y-%m-%d}&date_to={T0:%Y-%m-%d}")
    assert r.status_code == 200
    kachel = next(j for j in r.json()["jobs"] if j["id"] == str(szenario["job"].id))
    assert any(k["kind"] == "QUALIFIKATION" for k in kachel["conflicts"])

    # Die AKTE bleibt zu.
    r = c.get(f"/api/planung/mitarbeiter/{szenario['employee'].id}/qualifikationen")
    assert r.status_code == 403

    r = c.put(
        f"/api/planung/mitarbeiter/{szenario['employee'].id}/qualifikationen",
        {"qualification_id": str(gas.id)},
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_monteur_darf_den_katalog_nicht_pflegen(client_with_role):
    c = client_with_role("MONTEUR")
    r = c.post(
        "/api/planung/qualifikationen",
        {"code": "X", "label": "X"},
        content_type="application/json",
    )
    assert r.status_code == 403


# --- Zuweisungs-Vorlagen ----------------------------------------------------

@pytest.mark.django_db
def test_vorlage_anlegen_und_aendern(admin_client, szenario):
    r = admin_client.post(
        "/api/planung/vorlagen",
        {"name": "Bad-Team", "members": [
            {"app_user_id": str(szenario["login"].id), "role": "LEAD"},
        ]},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    t = r.json()
    assert t["name"] == "Bad-Team"
    assert t["members"][0]["role"] == "LEAD"
    assert t["members"][0]["display_name"] == "Timo Kalinski"

    # Mitglieder werden vollständig ersetzt; ohne das Feld bleiben sie stehen.
    r = admin_client.patch(
        f"/api/planung/vorlagen/{t['id']}",
        {"description": "Bad und Sanitär"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert len(r.json()["members"]) == 1

    r = admin_client.patch(
        f"/api/planung/vorlagen/{t['id']}",
        {"members": []},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["members"] == []
