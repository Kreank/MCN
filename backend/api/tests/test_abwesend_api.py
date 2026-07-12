"""„Derzeit abwesend" (Disposition) + Abwesenheits-Export (HR).

Die Trennlinie, um die es hier geht:

* `/api/planung/abwesend` — `workflow/LESEN`, das Recht der Disposition. Antwort:
  **wer** fehlt, **von wann bis wann**. **Nie die Art.** Die Art unterscheidet
  Urlaub von Krankheit und ist ein Gesundheitsdatum (DSGVO Art. 9).
* `/api/hr/abwesenheiten.csv` — mit Art, deshalb hinter `hr/EXPORTIEREN`. Für die
  Disposition und für NUR_LESEN: 403.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from api.tests.conftest import logged_in_client, make_app_user
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as ma


def _app_user_of(client):
    from django.contrib.auth import get_user_model

    uid = client.session["_auth_user_id"]
    return get_user_model().objects.get(pk=uid).app_user_id


def _employee(actor, nachname):
    person = identity_service.create_person(
        actor, first_name="Timo", last_name=nachname
    )
    emp = ma.create_employee(
        actor,
        app_user_id=make_app_user(nachname).id,
        party_id=person.id,
        hired_on=date(2026, 1, 1),
    )
    ma.create_contract(
        actor,
        employee_id=emp.id,
        valid_from=date(2026, 1, 1),
        hours={f"hours_{t}": Decimal("8") for t in
               ("monday", "tuesday", "wednesday", "thursday", "friday")},
        vacation_days_per_year=Decimal("30"),
    )
    return emp


def _abwesend(actor, emp, art, von, bis, *, genehmigen=True):
    a = ma.create_absence(
        actor, employee_id=emp.id, absence_type=art, start_date=von, end_date=bis
    )
    if genehmigen:
        ma.submit_absence(actor, absence_id=a.id)
        ma.approve_absence(actor, absence_id=a.id)
    return a


def _naechster_montag():
    heute = date.today()
    return heute + timedelta(days=(7 - heute.weekday()) % 7 or 7)


@pytest.fixture
def szene(admin_client):
    actor = _app_user_of(admin_client)
    kranker = _employee(actor, "Kranker")
    urlauber = _employee(actor, "Urlauber")
    montag = _naechster_montag()
    _abwesend(actor, kranker, "KRANKHEIT", montag, montag + timedelta(days=2))
    _abwesend(actor, urlauber, "URLAUB", montag, montag + timedelta(days=4))
    # Ein noch nicht genehmigter Antrag darf NIRGENDS auftauchen.
    dritter = _employee(actor, "Antragsteller")
    _abwesend(
        actor,
        dritter,
        "KRANKHEIT",
        montag,
        montag + timedelta(days=1),
        genehmigen=False,
    )
    return admin_client, actor, montag, kranker, urlauber


@pytest.mark.django_db
def test_derzeit_abwesend_nennt_keine_art(szene):
    admin, actor, montag, kranker, urlauber = szene
    dispo = logged_in_client("DISPOSITION")

    bis = montag + timedelta(days=4)
    r = dispo.get(f"/api/planung/abwesend?von={montag}&bis={bis}")
    assert r.status_code == 200, r.content
    body = r.json()
    assert len(body) == 2  # der ungenehmigte Antrag ist NICHT dabei

    roh = r.content.decode()
    assert "KRANKHEIT" not in roh and "URLAUB" not in roh
    assert "absence_type" not in roh
    for zeile in body:
        assert set(zeile) == {
            "id",
            "app_user_id",
            "name",
            "start_date",
            "end_date",
            "half_day_start",
            "half_day_end",
        }


@pytest.mark.django_db
def test_derzeit_abwesend_default_ist_heute(szene):
    """Ohne Zeitraum: der heutige Tag. (Der Zeitraum umspannt heute — ein
    einzelner Tag könnte ein Wochenende sein und wäre dann gar kein Fehltag.)"""
    admin, actor, montag, _, _ = szene
    heute = date.today()
    langzeit = _employee(actor, "Langzeit")
    a = _abwesend(
        actor,
        langzeit,
        "KRANKHEIT",
        heute - timedelta(days=6),
        heute + timedelta(days=6),
    )
    r = logged_in_client("DISPOSITION").get("/api/planung/abwesend")
    assert r.status_code == 200
    ids = [z["id"] for z in r.json()]
    assert str(a.id) in ids
    # Die Abwesenheiten des kommenden Montags gehören NICHT zu „heute".
    assert len(ids) == 1


@pytest.mark.django_db
def test_derzeit_abwesend_zeitraum_pruefung(szene):
    dispo = logged_in_client("DISPOSITION")
    assert dispo.get("/api/planung/abwesend?von=2026-07-10&bis=2026-07-01").status_code == 422
    assert dispo.get("/api/planung/abwesend?von=2020-01-01&bis=2026-01-01").status_code == 422


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_export_hinter_hr_recht(szene):
    admin, actor, montag, kranker, urlauber = szene
    jahr = montag.year

    r = admin.get(f"/api/hr/abwesenheiten.csv?von={jahr}-01-01&bis={jahr}-12-31")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    assert "Personalnummer;Mitarbeiter;Art;Von;Bis" in text
    assert "KRANKHEIT" in text and "URLAUB" in text
    # Auch der noch nicht genehmigte Antrag steht drin (die Personalverwaltung
    # führt den Bestand, nicht nur die Entscheidungen) — mit seinem Status.
    assert "ENTWURF" in text

    # Wer keine hr-Rechte hat, bekommt die Art nicht — auch nicht als CSV.
    for rolle in ("DISPOSITION", "MONTEUR", "NUR_LESEN"):
        c = logged_in_client(rolle)
        assert c.get("/api/hr/abwesenheiten.csv").status_code == 403, rolle


@pytest.mark.django_db
def test_export_filter(szene):
    admin, actor, montag, kranker, urlauber = szene
    jahr = montag.year
    r = admin.get(
        f"/api/hr/abwesenheiten.csv?von={jahr}-01-01&bis={jahr}-12-31"
        f"&employee_id={kranker.id}"
    )
    text = r.content.decode("utf-8-sig")
    assert "Kranker" in text and "Urlauber" not in text

    assert admin.get("/api/hr/abwesenheiten.csv?status=QUATSCH").status_code == 422
    assert admin.get(
        "/api/hr/abwesenheiten.csv?von=2026-07-10&bis=2026-07-01"
    ).status_code == 422
