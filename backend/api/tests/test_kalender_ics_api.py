"""API-Tests ICS-Export (`GET /api/kalender/...`).

Geprüft wird das, was beim Kalenderexport wirklich weh tut:

* **Rechte** — ein Monteur (row_scope EIGENE) bekommt einen fremden Einsatz als
  404 (nicht 403: die Existenz wird nicht verraten), und sein Zeitraum-Export
  enthält keinen einzigen fremden Termin.
* **Datenminimierung** — die Zutrittshinweise (`access_instructions`) tauchen
  NICHT in der Datei auf. Negativtest gegen den konkreten Text, nicht gegen ein
  Feldnamen-Muster: Die Datei landet in fremden Kalendern.
* **Absage** — AUSGEFALLEN wird als STATUS:CANCELLED exportiert statt
  weggelassen.
* **Betriebszeit** — `von`/`bis` sind Kalendertage in Europe/Berlin, nicht UTC.
"""
from datetime import datetime, timezone as dt_timezone

import pytest
from django.test import Client

from db_core.betriebszeit import BETRIEBS_TZ
from db_core.services import auftrag as auftrag_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

from .conftest import make_app_user, make_role_user

# 13.07.2026, 09:00–11:00 auf der Berliner Wanduhr (= 07:00–09:00 UTC, MESZ).
START = datetime(2026, 7, 13, 9, 0, tzinfo=BETRIEBS_TZ)
ENDE = datetime(2026, 7, 13, 11, 0, tzinfo=BETRIEBS_TZ)


def _client(role="ADMINISTRATION"):
    user, app_user = make_role_user(role)
    client = Client()
    client.force_login(user)
    return client, app_user


def _property(actor_id, name="Wartungsobjekt"):
    return property_service.create_property(
        actor_id, name=name, property_type="WEG",
        street="Steglitzer Damm", house_number="12",
        postal_code="12169", city="Berlin",
    )


def _order(actor_id, obj=None):
    obj = obj or _property(actor_id, name="Auftragshaus")
    principal = identity_service.create_person(
        actor_id, first_name="Petra", last_name="Prinzipal"
    )
    order = auftrag_service.create_work_order(
        actor_id, property_id=obj.id, title="Therme warten"
    )
    auftrag_service.set_order_evidence(
        actor_id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        actor_id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    auftrag_service.add_work_order_party(
        actor_id, work_order_id=order.id, party_id=principal.id,
        role="PRINCIPAL", is_primary=True,
    )
    for to_status in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(
            actor_id, work_order_id=order.id, to_status=to_status
        )
    return order


def _text(antwort):
    return antwort.content.decode("utf-8")


def _zeilen(text):
    """Entfaltete logische Zeilen (Fortsetzungszeilen wieder angehängt)."""
    logisch = []
    for z in text.split("\r\n"):
        if z.startswith(" ") and logisch:
            logisch[-1] += z[1:]
        else:
            logisch.append(z)
    return [z for z in logisch if z != ""]


# --- Einzelexport ----------------------------------------------------------

@pytest.mark.django_db
def test_einzelexport_liefert_ics_datei():
    client, app_user = _client()
    obj = _property(app_user.id)
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung Dachgeschoss", property_id=obj.id,
        scheduled_start=START, scheduled_end=ENDE,
    )
    r = client.get(f"/api/kalender/einsatz/{job.id}.ics")
    assert r.status_code == 200, r.content
    assert r["Content-Type"].startswith("text/calendar")
    assert r["X-Content-Type-Options"] == "nosniff"
    assert ".ics" in r["Content-Disposition"]
    assert job.job_number in r["Content-Disposition"]

    zeilen = _zeilen(_text(r))
    assert "BEGIN:VCALENDAR" in zeilen
    assert f"UID:{job.id}@einsatz.mcn" in zeilen
    # 09:00 Berlin im Juli = 07:00 UTC.
    assert "DTSTART:20260713T070000Z" in zeilen
    assert "DTEND:20260713T090000Z" in zeilen
    assert "SUMMARY:Begehung Dachgeschoss" in zeilen
    assert "LOCATION:Steglitzer Damm 12\\, 12169 Berlin" in zeilen


@pytest.mark.django_db
def test_zutrittshinweise_stehen_nicht_in_der_datei():
    """Der gefährlichste Fehler dieses Slice: Die Datei geht in fremde Kalender.
    Ein Schlüsselversteck darf dort nicht landen — auch nicht in DESCRIPTION."""
    client, app_user = _client()
    geheim = "Schluessel unter der Fussmatte, Code 4711"
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", scheduled_start=START, scheduled_end=ENDE,
        access_instructions=geheim,
    )
    r = client.get(f"/api/kalender/einsatz/{job.id}.ics")
    assert r.status_code == 200, r.content
    text = _text(r)
    assert geheim not in text
    assert "Fussmatte" not in text
    assert "4711" not in text
    # Gegenprobe: der Zutrittshinweis IST am Einsatz gespeichert, der Export
    # lässt ihn nur weg (sonst prüfte der Test nichts).
    job.refresh_from_db()
    assert job.access_instructions == geheim


@pytest.mark.django_db
def test_zugewiesene_namen_stehen_nicht_in_der_datei():
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("ADMINISTRATION")
    client = Client()
    client.force_login(user)
    job = einsatz_service.create_service_job(
        dispo.id, title="Begehung", scheduled_start=START, scheduled_end=ENDE
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    r = client.get(f"/api/kalender/einsatz/{job.id}.ics")
    assert r.status_code == 200, r.content
    text = _text(r)
    assert "ATTENDEE" not in text
    assert "ORGANIZER" not in text
    assert monteur.display_name not in text


@pytest.mark.django_db
def test_auftragsnummer_und_titel_kommen_aus_dem_auftrag():
    client, app_user = _client()
    order = _order(app_user.id)
    job = einsatz_service.create_service_job(
        app_user.id, work_order_id=order.id,
        scheduled_start=START, scheduled_end=ENDE,
    )
    r = client.get(f"/api/kalender/einsatz/{job.id}.ics")
    zeilen = _zeilen(_text(r))
    assert "SUMMARY:Therme warten" in zeilen
    beschreibung = [z for z in zeilen if z.startswith("DESCRIPTION:")][0]
    assert f"Auftrag: {order.order_number}" in beschreibung


@pytest.mark.django_db
def test_abgesagter_einsatz_wird_als_cancelled_exportiert():
    client, app_user = _client()
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", scheduled_start=START, scheduled_end=ENDE
    )
    # AUSGEFALLEN ist erst ab GEPLANT erreichbar (Statusautomat 0014).
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="GEPLANT"
    )
    einsatz_service.advance_status(
        app_user.id, service_job_id=job.id, to_status="AUSGEFALLEN",
        reason="Kunde abwesend",
    )
    r = client.get(f"/api/kalender/einsatz/{job.id}.ics")
    assert r.status_code == 200, r.content
    zeilen = _zeilen(_text(r))
    assert "BEGIN:VEVENT" in zeilen, "Der abgesagte Termin fehlt komplett."
    assert "STATUS:CANCELLED" in zeilen
    assert "SUMMARY:Abgesagt: Begehung" in zeilen
    # Der Absagegrund ist eine interne Notiz und gehört nicht in fremde Kalender.
    assert "Kunde abwesend" not in _text(r)


@pytest.mark.django_db
def test_ungeplanter_einsatz_422():
    client, app_user = _client()
    job = einsatz_service.create_service_job(app_user.id, title="Ohne Termin")
    r = client.get(f"/api/kalender/einsatz/{job.id}.ics")
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_unbekannter_einsatz_404():
    from uuid import uuid4

    client, _ = _client()
    r = client.get(f"/api/kalender/einsatz/{uuid4()}.ics")
    assert r.status_code == 404, r.content


# --- Rechte ----------------------------------------------------------------

@pytest.mark.django_db
def test_monteur_bekommt_fremden_einsatz_als_404():
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)

    meiner = einsatz_service.create_service_job(
        dispo.id, title="Meine Begehung", scheduled_start=START, scheduled_end=ENDE
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=meiner.id, assignee_user_id=monteur.id
    )
    fremder = einsatz_service.create_service_job(
        dispo.id, title="Fremde Begehung", scheduled_start=START, scheduled_end=ENDE
    )

    assert client.get(f"/api/kalender/einsatz/{meiner.id}.ics").status_code == 200
    r = client.get(f"/api/kalender/einsatz/{fremder.id}.ics")
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_monteur_zeitraum_enthaelt_keine_fremden_termine():
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    client = Client()
    client.force_login(user)

    meiner = einsatz_service.create_service_job(
        dispo.id, title="Meine Begehung", scheduled_start=START, scheduled_end=ENDE
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=meiner.id, assignee_user_id=monteur.id
    )
    fremder = einsatz_service.create_service_job(
        dispo.id, title="Fremde Begehung", scheduled_start=START, scheduled_end=ENDE
    )

    r = client.get("/api/kalender/einsaetze.ics?von=2026-07-13&bis=2026-07-13")
    assert r.status_code == 200, r.content
    text = _text(r)
    assert str(meiner.id) in text
    assert str(fremder.id) not in text
    assert "Fremde Begehung" not in text


@pytest.mark.django_db
def test_monteur_kann_assignee_id_nicht_umbiegen():
    """`assignee_id` ist bei Scope EIGENE kein Wunsch, sondern wird überschrieben."""
    dispo = make_app_user("Dispo")
    user, monteur = make_role_user("MONTEUR")
    kollege = make_app_user("Kollege")
    client = Client()
    client.force_login(user)

    fremder = einsatz_service.create_service_job(
        dispo.id, title="Fremde Begehung", scheduled_start=START, scheduled_end=ENDE
    )
    einsatz_service.assign_user(
        dispo.id, service_job_id=fremder.id, assignee_user_id=kollege.id
    )

    r = client.get(
        "/api/kalender/einsaetze.ics"
        f"?von=2026-07-13&bis=2026-07-13&assignee_id={kollege.id}"
    )
    assert r.status_code == 200, r.content
    assert "Fremde Begehung" not in _text(r)
    assert "BEGIN:VEVENT" not in _text(r)


@pytest.mark.django_db
def test_ohne_recht_403(client_with_role):
    client = client_with_role(None)
    r = client.get("/api/kalender/einsaetze.ics?von=2026-07-13&bis=2026-07-13")
    assert r.status_code == 403, r.content


# --- Zeitraum --------------------------------------------------------------

@pytest.mark.django_db
def test_zeitraum_export():
    client, app_user = _client()
    obj = _property(app_user.id)
    a = einsatz_service.create_service_job(
        app_user.id, title="Termin A", property_id=obj.id,
        scheduled_start=START, scheduled_end=ENDE,
    )
    b = einsatz_service.create_service_job(
        app_user.id, title="Termin B",
        scheduled_start=datetime(2026, 7, 20, 8, 0, tzinfo=BETRIEBS_TZ),
    )
    r = client.get("/api/kalender/einsaetze.ics?von=2026-07-01&bis=2026-07-31")
    assert r.status_code == 200, r.content
    text = _text(r)
    assert _zeilen(text).count("BEGIN:VEVENT") == 2
    assert str(a.id) in text and str(b.id) in text
    assert "einsaetze-2026-07-01_2026-07-31.ics" in r["Content-Disposition"]


@pytest.mark.django_db
def test_zeitraum_ist_betriebszeit_nicht_utc():
    """Ein Termin am 01.07. um 00:30 Berlin ist in UTC noch der 30.06. Wer nach
    UTC-Tagen filtert, verliert ihn aus dem Fenster „ab dem 1." — genau der
    Fehler aus Invariante Kap. 7."""
    client, app_user = _client()
    nachts = datetime(2026, 7, 1, 0, 30, tzinfo=BETRIEBS_TZ)
    assert nachts.astimezone(dt_timezone.utc).date().isoformat() == "2026-06-30"
    job = einsatz_service.create_service_job(
        app_user.id, title="Nachteinsatz", scheduled_start=nachts
    )
    r = client.get("/api/kalender/einsaetze.ics?von=2026-07-01&bis=2026-07-01")
    assert r.status_code == 200, r.content
    assert str(job.id) in _text(r)

    # Und er gehört NICHT in den Juni.
    r = client.get("/api/kalender/einsaetze.ics?von=2026-06-01&bis=2026-06-30")
    assert str(job.id) not in _text(r)


@pytest.mark.django_db
def test_ungeplante_fallen_aus_dem_zeitraum():
    client, app_user = _client()
    einsatz_service.create_service_job(app_user.id, title="Ohne Termin")
    r = client.get("/api/kalender/einsaetze.ics?von=2026-07-01&bis=2026-07-31")
    assert r.status_code == 200, r.content
    assert "BEGIN:VEVENT" not in _text(r)


@pytest.mark.django_db
def test_zeitraum_ohne_parameter_422():
    client, _ = _client()
    assert client.get("/api/kalender/einsaetze.ics").status_code == 422


@pytest.mark.django_db
def test_zeitraum_verkehrt_herum_422():
    client, _ = _client()
    r = client.get("/api/kalender/einsaetze.ics?von=2026-07-31&bis=2026-07-01")
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_zu_langer_zeitraum_422():
    client, _ = _client()
    r = client.get("/api/kalender/einsaetze.ics?von=2026-01-01&bis=2027-12-31")
    assert r.status_code == 422, r.content
    assert "366" in r.json()["detail"]


@pytest.mark.django_db
def test_ein_ganzes_jahr_ist_erlaubt():
    client, _ = _client()
    r = client.get("/api/kalender/einsaetze.ics?von=2026-01-01&bis=2026-12-31")
    assert r.status_code == 200, r.content
