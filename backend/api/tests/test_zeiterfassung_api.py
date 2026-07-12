"""API-Tests der Zeiterfassung — vor allem die Rechte (DSGVO: Zeiten sind
Personendaten; kein Monteur sieht fremde Zeiten).

MONTEUR trägt seit Migration 0068 `hr/LESEN` + `hr/AENDERN` mit row_scope
EIGENE. Die Verwaltungssicht (`require`) bleibt für ihn fail-closed auf 403.
"""
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api.tests.conftest import grant_role, logged_in_client, make_app_user
from db_core.models import TimeCategory, TimeEntry, WorkDay
from db_core.services import zeiterfassung as zeit

TZ = ZoneInfo("Europe/Berlin")


def _dt(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def _kat(code):
    return TimeCategory.objects.get(code=code)


@pytest.fixture
def monteur(db):
    c = logged_in_client("MONTEUR")
    return c, c.session.get("_auth_user_id")


def _app_user_of(client):
    from django.contrib.auth import get_user_model

    uid = client.session["_auth_user_id"]
    return get_user_model().objects.get(pk=uid).app_user_id


# ---------------------------------------------------------------------------
# Stempeluhr
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_stempeluhr_durchlauf(client_with_role):
    c = client_with_role("MONTEUR")

    r = c.get("/api/zeiterfassung/aktuell")
    assert r.status_code == 200
    assert r.json()["laeuft"] is False
    assert r.json()["zustand"] == "GESTOPPT"

    r = c.post(
        "/api/zeiterfassung/stempel/start", data="{}", content_type="application/json"
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["laeuft"] is True and body["zustand"] == "LAEUFT"
    assert body["eintrag"]["kategorie"] == "Arbeitszeit"
    assert body["eintrag"]["ended_at"] is None
    assert body["work_day_id"] is not None
    assert body["tagesstatus"] == "ENTWURF"

    r = c.post("/api/zeiterfassung/stempel/pause")
    assert r.status_code == 200 and r.json()["zustand"] == "PAUSE"

    r = c.post("/api/zeiterfassung/stempel/weiter")
    assert r.status_code == 200 and r.json()["zustand"] == "LAEUFT"

    r = c.post("/api/zeiterfassung/stempel/stopp")
    assert r.status_code == 200
    body = r.json()
    assert body["laeuft"] is False
    # Die Summen tragen ihren Bezugstag mit (S7) — „heute" wird nicht behauptet.
    assert body["tag_arbeit_sekunden"] >= 0
    assert body["tag"] == date.today().isoformat()


@pytest.mark.django_db
def test_doppeltes_start_422(client_with_role):
    c = client_with_role("MONTEUR")
    c.post("/api/zeiterfassung/stempel/start", data="{}",
           content_type="application/json")
    r = c.post("/api/zeiterfassung/stempel/start", data="{}",
               content_type="application/json")
    assert r.status_code == 422


@pytest.mark.django_db
def test_pause_ohne_arbeit_422(client_with_role):
    c = client_with_role("MONTEUR")
    assert c.post("/api/zeiterfassung/stempel/pause").status_code == 422
    assert c.post("/api/zeiterfassung/stempel/weiter").status_code == 422
    assert c.post("/api/zeiterfassung/stempel/stopp").status_code == 422


@pytest.mark.django_db
def test_stempel_kennt_keine_fremde_user_id(client_with_role):
    """Der Monteur kann sich nicht als jemand anderes stempeln — die API nimmt
    hier gar keine user_id entgegen (das ist die stärkste Form der Sperre)."""
    c = client_with_role("MONTEUR")
    fremd = make_app_user("Fremder")
    r = c.post(
        "/api/zeiterfassung/stempel/start",
        data=f'{{"note": "x", "user_id": "{fremd.id}"}}',
        content_type="application/json",
    )
    assert r.status_code == 200
    actor = _app_user_of(c)
    assert TimeEntry.objects.filter(user_id=fremd.id).count() == 0
    assert TimeEntry.objects.filter(user_id=actor).count() == 1


# ---------------------------------------------------------------------------
# Rechte / DSGVO
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_monteur_sieht_verwaltungssicht_nicht(client_with_role):
    c = client_with_role("MONTEUR")
    assert c.get("/api/zeiterfassung").status_code == 403
    assert c.get("/api/zeiterfassung/stundenliste.csv").status_code == 403


@pytest.mark.django_db
def test_monteur_sieht_nur_eigene_tage(client_with_role):
    a = client_with_role("MONTEUR")
    b = client_with_role("MONTEUR")
    a.post("/api/zeiterfassung/stempel/start", data="{}",
           content_type="application/json")
    a.post("/api/zeiterfassung/stempel/stopp")
    b.post("/api/zeiterfassung/stempel/start", data="{}",
           content_type="application/json")
    b.post("/api/zeiterfassung/stempel/stopp")

    tage_a = a.get("/api/zeiterfassung/meine-tage").json()
    assert len(tage_a) == 1
    assert tage_a[0]["user_id"] == str(_app_user_of(a))

    # Der fremde Tag ist 404 (Existenz wird nicht verraten), nicht 403.
    fremder_tag = b.get("/api/zeiterfassung/meine-tage").json()[0]["id"]
    assert a.get(f"/api/zeiterfassung/tage/{fremder_tag}").status_code == 404
    assert (
        a.post(f"/api/zeiterfassung/tage/{fremder_tag}/einreichen").status_code == 404
    )


@pytest.mark.django_db
def test_monteur_kann_keine_fremde_zeit_erfassen(client_with_role):
    c = client_with_role("MONTEUR")
    fremd = make_app_user("Fremder")
    r = c.post(
        "/api/zeiterfassung/eintraege",
        data={
            "category_id": str(_kat("ARBEITSZEIT").id),
            "user_id": str(fremd.id),
            "started_at": "2026-07-13T08:00:00+02:00",
            "ended_at": "2026-07-13T12:00:00+02:00",
        },
        content_type="application/json",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_monteur_fremder_eintrag_404(client_with_role):
    a = client_with_role("MONTEUR")
    b = client_with_role("MONTEUR")
    r = b.post(
        "/api/zeiterfassung/eintraege",
        data={
            "category_id": str(_kat("ARBEITSZEIT").id),
            "started_at": "2026-07-13T08:00:00+02:00",
            "ended_at": "2026-07-13T12:00:00+02:00",
        },
        content_type="application/json",
    )
    assert r.status_code == 201
    fremd_id = r.json()["id"]
    assert (
        a.patch(
            f"/api/zeiterfassung/eintraege/{fremd_id}",
            data={"note": "geklaut"},
            content_type="application/json",
        ).status_code
        == 404
    )
    assert a.delete(f"/api/zeiterfassung/eintraege/{fremd_id}").status_code == 404


@pytest.mark.django_db
def test_monteur_darf_nicht_bestaetigen(client_with_role, admin_client):
    c = client_with_role("MONTEUR")
    c.post("/api/zeiterfassung/stempel/start", data="{}",
           content_type="application/json")
    c.post("/api/zeiterfassung/stempel/stopp")
    tag = c.get("/api/zeiterfassung/meine-tage").json()[0]["id"]
    c.post(f"/api/zeiterfassung/tage/{tag}/einreichen")

    assert c.post(f"/api/zeiterfassung/tage/{tag}/bestaetigen").status_code == 403
    r = admin_client.post(f"/api/zeiterfassung/tage/{tag}/bestaetigen")
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "BESTAETIGT"


@pytest.mark.django_db
def test_bestaetigter_tag_sperrt_die_uhr_nicht_dauerhaft(client_with_role, admin_client):
    """Review-Befund S4: der Tag wird mittags bestätigt — der Monteur muss
    weiterstempeln können. Ohne Begründung 422 mit einer FACHLICHEN Meldung
    (nie die rohe DB-Meldung „SET LOCAL …"), mit Begründung läuft die Uhr und der
    Tag fällt auf ENTWURF zurück."""
    c = client_with_role("MONTEUR")
    c.post("/api/zeiterfassung/stempel/start", data="{}",
           content_type="application/json")
    c.post("/api/zeiterfassung/stempel/stopp")
    tag = c.get("/api/zeiterfassung/meine-tage").json()[0]["id"]
    c.post(f"/api/zeiterfassung/tage/{tag}/einreichen")
    assert admin_client.post(
        f"/api/zeiterfassung/tage/{tag}/bestaetigen"
    ).status_code == 200

    r = c.post("/api/zeiterfassung/stempel/start", data="{}",
               content_type="application/json")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "SET LOCAL" not in detail
    assert "Begründung" in detail

    r = c.post(
        "/api/zeiterfassung/stempel/start",
        data='{"correction_reason": "Nachmittagseinsatz kam dazu"}',
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["laeuft"] is True
    assert r.json()["tagesstatus"] == "ENTWURF"


@pytest.mark.django_db
def test_vier_augen_ueber_die_api(admin_client):
    """Auch der Admin darf seinen EIGENEN Tag nicht bestätigen (422)."""
    admin_client.post("/api/zeiterfassung/stempel/start", data="{}",
                      content_type="application/json")
    admin_client.post("/api/zeiterfassung/stempel/stopp")
    tag = admin_client.get("/api/zeiterfassung/meine-tage").json()[0]["id"]
    admin_client.post(f"/api/zeiterfassung/tage/{tag}/einreichen")
    r = admin_client.post(f"/api/zeiterfassung/tage/{tag}/bestaetigen")
    assert r.status_code == 422
    assert "Vier-Augen" in r.json()["detail"]


@pytest.mark.django_db
def test_ablehnen_ohne_begruendung_422(client_with_role, admin_client):
    c = client_with_role("MONTEUR")
    c.post("/api/zeiterfassung/stempel/start", data="{}",
           content_type="application/json")
    c.post("/api/zeiterfassung/stempel/stopp")
    tag = c.get("/api/zeiterfassung/meine-tage").json()[0]["id"]
    c.post(f"/api/zeiterfassung/tage/{tag}/einreichen")

    r = admin_client.post(
        f"/api/zeiterfassung/tage/{tag}/ablehnen",
        data={"note": "  "},
        content_type="application/json",
    )
    assert r.status_code == 422
    r = admin_client.post(
        f"/api/zeiterfassung/tage/{tag}/ablehnen",
        data={"note": "Fahrtzeit fehlt"},
        content_type="application/json",
    )
    assert r.status_code == 200 and r.json()["status"] == "ABGELEHNT"


# ---------------------------------------------------------------------------
# Verwaltung, Korrektur, Export
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_verwaltungsliste_und_filter(admin_client):
    admin_client.post("/api/zeiterfassung/stempel/start", data="{}",
                      content_type="application/json")
    admin_client.post("/api/zeiterfassung/stempel/stopp")
    r = admin_client.get("/api/zeiterfassung?zeitraum=heute")
    assert r.status_code == 200 and len(r.json()) == 1
    assert admin_client.get("/api/zeiterfassung?zeitraum=jahr").status_code == 200
    assert admin_client.get("/api/zeiterfassung?zeitraum=quatsch").status_code == 422
    assert admin_client.get("/api/zeiterfassung?status=UNBEKANNT").status_code == 422
    r = admin_client.get("/api/zeiterfassung?zeitraum=heute&status=BESTAETIGT")
    assert r.json() == []


@pytest.mark.django_db
def test_korrektur_eines_bestaetigten_tages(client_with_role, admin_client):
    c = client_with_role("MONTEUR")
    actor = _app_user_of(c)
    zeit.zeiteintrag_anlegen(
        actor, user_id=actor, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 16),
    )
    tag = WorkDay.objects.get(user_id=actor, day=date(2026, 7, 13))
    c.post(f"/api/zeiterfassung/tage/{tag.id}/einreichen")
    admin_client.post(f"/api/zeiterfassung/tage/{tag.id}/bestaetigen")

    # Ohne Begründung: 422 (das Arbeitstag-Schloss).
    payload = {
        "category_id": str(_kat("FAHRTZEIT").id),
        "started_at": "2026-07-13T17:00:00+02:00",
        "ended_at": "2026-07-13T18:00:00+02:00",
    }
    r = c.post("/api/zeiterfassung/eintraege", data=payload,
               content_type="application/json")
    assert r.status_code == 422

    # Mit Begründung: geht durch, der Tag fällt auf ENTWURF zurück.
    r = c.post(
        "/api/zeiterfassung/eintraege",
        data={**payload, "correction_reason": "Rückfahrt nachgetragen"},
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    tag.refresh_from_db()
    assert tag.status == "ENTWURF"


@pytest.mark.django_db
def test_pausen_anwenden_ueber_api(client_with_role):
    c = client_with_role("MONTEUR")
    actor = _app_user_of(c)
    zeit.zeiteintrag_anlegen(
        actor, user_id=actor, category_id=_kat("ARBEITSZEIT").id,
        started_at=_dt(2026, 7, 13, 8), ended_at=_dt(2026, 7, 13, 16),
    )
    tag = WorkDay.objects.get(user_id=actor, day=date(2026, 7, 13))
    r = c.post(f"/api/zeiterfassung/tage/{tag.id}/pausen-anwenden")
    assert r.status_code == 200
    body = r.json()
    assert body["pause_sekunden"] == 1800
    auto = [e for e in body["eintraege"] if e["auto_generated"]]
    assert len(auto) == 1 and auto[0]["kategorie"] == "Pause"


@pytest.mark.django_db
def test_stundenliste_csv(admin_client):
    admin_client.post("/api/zeiterfassung/stempel/start", data="{}",
                      content_type="application/json")
    admin_client.post("/api/zeiterfassung/stempel/stopp")
    r = admin_client.get("/api/zeiterfassung/stundenliste.csv")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    assert "Mitarbeiter;Tag;Beginn;Ende;Dauer (h)" in text
    assert "Arbeitszeit" in text


@pytest.mark.django_db
def test_stundenkonto_ohne_personalsatz_404(admin_client):
    assert admin_client.get("/api/zeiterfassung/stundenkonto").status_code == 404


@pytest.mark.django_db
def test_mitarbeitende_filterliste(admin_client, client_with_role):
    """Der Browser-Durchlauf hat hier einen 500 aufgedeckt: `Employee.party`
    zeigt auf identity.person, nicht auf identity.party — `display_name` gibt es
    dort nicht."""
    from datetime import date as _date
    from decimal import Decimal

    from db_core.services import identity as identity_service
    from db_core.services import mitarbeiter as mitarbeiter_service

    actor = _app_user_of(admin_client)
    ma = make_app_user("Monteur-Konto")
    person = identity_service.create_person(actor, first_name="Max", last_name="Monteur")
    emp = mitarbeiter_service.create_employee(
        actor, app_user_id=ma.id, party_id=person.id, hired_on=_date(2026, 1, 1)
    )

    r = admin_client.get("/api/zeiterfassung/mitarbeitende")
    assert r.status_code == 200, r.content
    body = r.json()
    assert any(
        m["user_id"] == str(ma.id)
        and m["employee_id"] == str(emp.id)
        and m["name"] == "Max Monteur"
        for m in body
    )

    # fail-closed: der Monteur bekommt die Personalliste nicht.
    assert client_with_role("MONTEUR").get(
        "/api/zeiterfassung/mitarbeitende"
    ).status_code == 403


# ---------------------------------------------------------------------------
# Stammdaten
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_kategorien_lesen_darf_auch_der_monteur(client_with_role):
    c = client_with_role("MONTEUR")
    r = c.get("/api/hr/zeitkategorien")
    assert r.status_code == 200
    codes = {k["code"] for k in r.json() if k["code"]}
    assert "ARBEITSZEIT" in codes and "PAUSE" in codes
    # Pflegen darf er nicht.
    assert (
        c.post(
            "/api/hr/zeitkategorien",
            data={"name": "Heimlich", "is_work_time": True},
            content_type="application/json",
        ).status_code
        == 403
    )


@pytest.mark.django_db
def test_kategorie_crud(admin_client):
    r = admin_client.post(
        "/api/hr/zeitkategorien",
        data={"name": "Rüstzeit", "is_work_time": True, "sort_order": 200},
        content_type="application/json",
    )
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["is_system"] is False

    r = admin_client.patch(
        f"/api/hr/zeitkategorien/{cid}",
        data={"name": "Rüstzeit Werkstatt"},
        content_type="application/json",
    )
    assert r.status_code == 200 and r.json()["name"] == "Rüstzeit Werkstatt"

    r = admin_client.post(f"/api/hr/zeitkategorien/{cid}/archivieren")
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVIERT"

    # Systemkategorie: nicht archivierbar.
    sys_id = _kat("ARBEITSZEIT").id
    r = admin_client.post(f"/api/hr/zeitkategorien/{sys_id}/archivieren")
    assert r.status_code == 422


@pytest.mark.django_db
def test_pausenregel_lesen_und_setzen(admin_client, client_with_role):
    r = admin_client.get("/api/hr/pausenregel")
    assert r.status_code == 200 and r.json()["mode"] == "GESETZLICH"

    r = admin_client.put(
        "/api/hr/pausenregel",
        data={"mode": "FESTE_ZEITEN",
              "fixed_breaks": [{"von": "12:00", "bis": "12:30"}]},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json()["fixed_breaks"] == [{"von": "12:00", "bis": "12:30"}]

    r = admin_client.put(
        "/api/hr/pausenregel",
        data={"mode": "FESTE_ZEITEN", "fixed_breaks": []},
        content_type="application/json",
    )
    assert r.status_code == 422

    # Der Monteur darf die Regel lesen (er sieht die Pausen), nicht ändern.
    c = client_with_role("MONTEUR")
    assert c.get("/api/hr/pausenregel").status_code == 200
    assert (
        c.put(
            "/api/hr/pausenregel",
            data={"mode": "KEINE"},
            content_type="application/json",
        ).status_code
        == 403
    )


@pytest.mark.django_db
def test_feiertage(admin_client):
    r = admin_client.get("/api/hr/feiertage?jahr=2026")
    assert r.status_code == 200
    namen = {f["name"] for f in r.json()}
    assert "Neujahr" in namen and "Tag der Deutschen Einheit" in namen


@pytest.mark.django_db
def test_anonym_401(anonymous_client):
    assert anonymous_client.get("/api/zeiterfassung/aktuell").status_code == 401
    assert (
        anonymous_client.post("/api/zeiterfassung/stempel/start").status_code == 401
    )
