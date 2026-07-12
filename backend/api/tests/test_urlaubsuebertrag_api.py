"""Resturlaubs-Übertrag ins Folgejahr + Verfallsregel (Migration 0072).

Kernaussagen, die hier festgenagelt werden:

* Der Rest wird **ermittelt**, nicht getippt: Anspruch + Übertrag + Anpassung
  − Verbrauch (aus genehmigten URLAUB-Abwesenheiten, halbe Tage inklusive).
* Der Übertrag ist **idempotent** (er SETZT, er addiert nicht).
* Die Verfallsregel ist eine **Firmeneinstellung**. Ohne sie verfällt nichts.
"""
from datetime import date
from decimal import Decimal

import pytest

from api.tests.conftest import logged_in_client, make_app_user
from db_core.models import CompanyProfile, VacationBudget
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as ma


def _app_user_of(client):
    from django.contrib.auth import get_user_model

    uid = client.session["_auth_user_id"]
    return get_user_model().objects.get(pk=uid).app_user_id


def _employee(actor, nachname="Kalinski", urlaub=Decimal("30")):
    person = identity_service.create_person(
        actor, first_name="Timo", last_name=nachname
    )
    emp = ma.create_employee(
        actor,
        app_user_id=make_app_user(nachname).id,
        party_id=person.id,
        hired_on=date(2025, 1, 1),
    )
    ma.create_contract(
        actor,
        employee_id=emp.id,
        valid_from=date(2025, 1, 1),
        hours={f"hours_{t}": Decimal("8") for t in
               ("monday", "tuesday", "wednesday", "thursday", "friday")},
        vacation_days_per_year=urlaub,
    )
    return emp


def _urlaub(actor, emp, von, bis, *, half_start=False, half_end=False):
    a = ma.create_absence(
        actor,
        employee_id=emp.id,
        absence_type="URLAUB",
        start_date=von,
        end_date=bis,
        half_day_start=half_start,
        half_day_end=half_end,
    )
    ma.submit_absence(actor, absence_id=a.id)
    ma.approve_absence(actor, absence_id=a.id)
    return a


@pytest.fixture
def szene(admin_client):
    actor = _app_user_of(admin_client)
    emp = _employee(actor)
    return admin_client, actor, emp


# ---------------------------------------------------------------------------
# Ermittlung des Rests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_rest_ermittlung_mit_halben_tagen_uebertrag_und_anpassung(szene):
    admin, actor, emp = szene
    ma.set_vacation_budget(
        actor,
        employee_id=emp.id,
        year=2026,
        entitlement_days=Decimal("30"),
        carryover_days=Decimal("5"),
        adjustment_days=Decimal("-2"),
        adjustment_reason="Unterjähriger Wechsel auf 4-Tage-Woche",
    )
    # Mo 06.07. – Fr 10.07.2026 = 5 Arbeitstage, halber Starttag → 4,5.
    _urlaub(actor, emp, date(2026, 7, 6), date(2026, 7, 10), half_start=True)
    # Mo 03.08. – Di 04.08.2026 = 2 Tage.
    _urlaub(actor, emp, date(2026, 8, 3), date(2026, 8, 4))

    konto = ma.vacation_account(emp.id, 2026)
    assert konto["total_days"] == Decimal("33")     # 30 + 5 − 2
    assert konto["used_days"] == Decimal("6.5")     # 4,5 + 2
    assert konto["expired_days"] == Decimal("0")    # keine Verfallsregel gesetzt
    assert konto["remaining_days"] == Decimal("26.5")

    r = admin.get("/api/hr/urlaubsuebertrag/vorschau?year=2026")
    assert r.status_code == 200, r.content
    zeile = next(z for z in r.json() if z["employee_id"] == str(emp.id))
    assert zeile["remaining_days"] == "26.50"
    assert zeile["carryover_current"] == "0.00"
    assert zeile["carryover_new"] == "26.50"
    assert zeile["target_year"] == 2027
    assert zeile["changes"] is True


@pytest.mark.django_db
def test_uebertrag_ist_idempotent(szene):
    admin, actor, emp = szene
    ma.set_vacation_budget(
        actor, employee_id=emp.id, year=2026, entitlement_days=Decimal("30")
    )
    _urlaub(actor, emp, date(2026, 7, 6), date(2026, 7, 10))  # 5 Tage

    for _ in range(2):
        r = admin.post(
            "/api/hr/urlaubsuebertrag",
            data={"year": 2026, "employee_ids": [str(emp.id)]},
            content_type="application/json",
        )
        assert r.status_code == 200, r.content

    budgets = VacationBudget.objects.filter(employee_id=emp.id, year=2027)
    assert budgets.count() == 1
    budget = budgets.first()
    # 30 − 5 = 25 — zweimal gedrückt bleibt es bei 25 (SETZEN, nicht ADDIEREN).
    assert budget.carryover_days == Decimal("25.00")
    # Der Anspruch des Folgejahres kommt aus dem gültigen Vertrag.
    assert budget.entitlement_days == Decimal("30.00")

    # Und der Rest von 2027 rechnet den Übertrag mit.
    assert ma.vacation_account(emp.id, 2027)["total_days"] == Decimal("55")


@pytest.mark.django_db
def test_uebertrag_kappt_negativen_rest_auf_null(szene):
    admin, actor, emp = szene
    ma.set_vacation_budget(
        actor,
        employee_id=emp.id,
        year=2026,
        entitlement_days=Decimal("2"),
        adjustment_days=Decimal("-5"),
        adjustment_reason="Vorschuss aus dem Vorjahr",
    )
    assert ma.vacation_account(emp.id, 2026)["remaining_days"] == Decimal("-3")
    admin.post(
        "/api/hr/urlaubsuebertrag",
        data={"year": 2026, "employee_ids": [str(emp.id)]},
        content_type="application/json",
    )
    assert (
        VacationBudget.objects.get(employee_id=emp.id, year=2027).carryover_days
        == Decimal("0.00")
    )


@pytest.mark.django_db
def test_ausgetretene_bekommen_keinen_uebertrag(szene):
    """§ 7 Abs. 4 BUrlG: Resturlaub wird abgegolten, nicht übertragen."""
    admin, actor, emp = szene
    ma.set_employee_status(
        actor, employee_id=emp.id, status="AUSGETRETEN", left_on=date(2026, 12, 31)
    )
    r = admin.get("/api/hr/urlaubsuebertrag/vorschau?year=2026")
    assert all(z["employee_id"] != str(emp.id) for z in r.json())


# ---------------------------------------------------------------------------
# Verfallsregel — an und aus
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ohne_verfallsregel_verfaellt_nichts(szene):
    """Der Default: NULL/NULL. Es wird nichts weggerechnet, was der Betrieb
    nicht ausdrücklich eingestellt hat."""
    admin, actor, emp = szene
    ma.set_vacation_budget(
        actor,
        employee_id=emp.id,
        year=2026,
        entitlement_days=Decimal("30"),
        carryover_days=Decimal("8"),
    )
    konto = ma.vacation_account(emp.id, 2026, today=date(2026, 12, 31))
    assert konto["expiry_date"] is None
    assert konto["expired_days"] == Decimal("0")
    assert konto["remaining_days"] == Decimal("38")


@pytest.mark.django_db
def test_verfallsregel_laesst_den_unverbrauchten_uebertrag_verfallen(szene):
    admin, actor, emp = szene
    firma_service.update_company_profile(
        actor,
        company_name="Mitra Sanitär",
        vacation_carryover_expiry_month=3,
        vacation_carryover_expiry_day=31,
    )
    ma.set_vacation_budget(
        actor,
        employee_id=emp.id,
        year=2026,
        entitlement_days=Decimal("30"),
        carryover_days=Decimal("8"),
    )
    # 3 Tage im Februar genommen — sie zehren zuerst am Übertrag.
    _urlaub(actor, emp, date(2026, 2, 2), date(2026, 2, 4))

    # VOR dem Verfallstag ist nichts verfallen.
    konto = ma.vacation_account(emp.id, 2026, today=date(2026, 3, 30))
    assert konto["expiry_date"] == date(2026, 3, 31)
    assert konto["expired_days"] == Decimal("0")
    assert konto["remaining_days"] == Decimal("35")  # 38 − 3

    # NACH dem Verfallstag verfallen die ungenutzten 5 Übertragstage.
    konto = ma.vacation_account(emp.id, 2026, today=date(2026, 4, 1))
    assert konto["expired_days"] == Decimal("5")
    assert konto["remaining_days"] == Decimal("30")  # 38 − 3 − 5

    # Und der Übertrag ins Folgejahr rechnet mit dem verfallenen Rest.
    r = admin.get("/api/hr/urlaubsuebertrag/vorschau?year=2026")
    zeile = next(z for z in r.json() if z["employee_id"] == str(emp.id))
    assert zeile["expired_days"] == "5.00"
    assert zeile["carryover_new"] == "30.00"


@pytest.mark.django_db
def test_urlaub_ueber_den_stichtag_zaehlt_nur_anteilig(szene):
    """Review-Befund A3: Ein Urlaub, der den Verfallstag überspannt, wurde
    VOLLSTÄNDIG als „bis zum Stichtag verbraucht" gezählt — es verfiel zu wenig.

    Szenario: Verfall zum 31.03., Übertrag 10 Tage, Urlaub 30.03.–10.04.
    Vor dem Stichtag liegen nur Mo 30.03. und Di 31.03. = **2 Tage**; die
    restlichen 8 Tage im April zehren nicht mehr am Übertrag. Es verfallen also
    10 − 2 = **8 Tage**, nicht 0.
    """
    admin, actor, emp = szene
    firma_service.update_company_profile(
        actor,
        company_name="Mitra Sanitär",
        vacation_carryover_expiry_month=3,
        vacation_carryover_expiry_day=31,
    )
    ma.set_vacation_budget(
        actor,
        employee_id=emp.id,
        year=2026,
        entitlement_days=Decimal("30"),
        carryover_days=Decimal("10"),
    )
    # 30.03.2026 ist ein Montag; 10.04.2026 ein Freitag → 10 Arbeitstage.
    a = _urlaub(actor, emp, date(2026, 3, 30), date(2026, 4, 10))
    assert a.days_count == Decimal("10.00")

    konto = ma.vacation_account(emp.id, 2026, today=date(2026, 5, 1))
    assert konto["used_days"] == Decimal("10.00")
    assert konto["expired_days"] == Decimal("8")     # 10 Übertrag − 2 bis 31.03.
    assert konto["remaining_days"] == Decimal("22")  # 40 − 10 − 8


@pytest.mark.django_db
def test_verfall_greift_nicht_wenn_der_uebertrag_verbraucht_wurde(szene):
    admin, actor, emp = szene
    firma_service.update_company_profile(
        actor,
        company_name="Mitra Sanitär",
        vacation_carryover_expiry_month=3,
        vacation_carryover_expiry_day=31,
    )
    ma.set_vacation_budget(
        actor,
        employee_id=emp.id,
        year=2026,
        entitlement_days=Decimal("30"),
        carryover_days=Decimal("5"),
    )
    # 5 Arbeitstage im März (Mo–Fr) — der Übertrag ist damit aufgebraucht.
    _urlaub(actor, emp, date(2026, 3, 2), date(2026, 3, 6))
    konto = ma.vacation_account(emp.id, 2026, today=date(2026, 6, 1))
    assert konto["expired_days"] == Decimal("0")
    assert konto["remaining_days"] == Decimal("30")


@pytest.mark.django_db
def test_verfallsregel_braucht_tag_und_monat(admin_client):
    """Nur den Monat setzen → 422 (nicht 500 aus dem DB-CHECK)."""
    r = admin_client.put(
        "/api/company/profile",
        data={"company_name": "Mitra", "vacation_carryover_expiry_month": 3},
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Tag UND Monat" in r.json()["detail"]

    r = admin_client.put(
        "/api/company/profile",
        data={
            "company_name": "Mitra",
            "vacation_carryover_expiry_month": 2,
            "vacation_carryover_expiry_day": 31,
        },
        content_type="application/json",
    )
    assert r.status_code == 422

    r = admin_client.put(
        "/api/company/profile",
        data={
            "company_name": "Mitra",
            "vacation_carryover_expiry_month": 3,
            "vacation_carryover_expiry_day": 31,
        },
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["vacation_carryover_expiry_day"] == 31
    p = CompanyProfile.objects.first()
    assert p.vacation_carryover_expiry_month == 3


# ---------------------------------------------------------------------------
# Rechte
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_uebertrag_hinter_hr_recht(szene):
    admin, actor, emp = szene
    dispo = logged_in_client("DISPOSITION")
    monteur = logged_in_client("MONTEUR")
    for c in (dispo, monteur):
        assert c.get("/api/hr/urlaubsuebertrag/vorschau?year=2026").status_code == 403
        assert (
            c.post(
                "/api/hr/urlaubsuebertrag",
                data={"year": 2026},
                content_type="application/json",
            ).status_code
            == 403
        )
