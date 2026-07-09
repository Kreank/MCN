"""Tests für den Personal-Service (hr.*, Migration 0019).

Die Test-DB baut die volle Migrationskette, die Trigger und EXCLUDE-Constraints
sind also scharf. Wo der Service eine Regel vorab prüft, testen wir zusätzlich,
dass die Datenbank sie ebenfalls durchsetzt (Umgehung am Service vorbei).
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
# Trigger-Verstöße kommen als SQLSTATE P0001 (RAISE EXCEPTION) an; Django
# reicht sie als ProgrammingError durch. Im Service übersetzt sie
# gate_errors.as_business_error in einen ValueError (→ 422).
from django.db.utils import IntegrityError, ProgrammingError

from db_core.db_context import business_transaction
from db_core.models import Absence, AppUser, Employee, EmploymentContract
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as service

# Vollzeit Mo–Fr, 8 Stunden, Wochenende frei.
VOLLZEIT = {
    "hours_monday": 8,
    "hours_tuesday": 8,
    "hours_wednesday": 8,
    "hours_thursday": 8,
    "hours_friday": 8,
}


def _person(app_user, first_name="Anna", last_name="Muster"):
    return identity_service.create_person(app_user.id, first_name, last_name)


def _account(display_name="Monteur Konto"):
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=display_name, status="ACTIVE", version=1
    )


def _employee(app_user, hired_on=date(2024, 1, 1), **kwargs):
    person = _person(app_user, **kwargs)
    return service.create_employee(
        app_user.id,
        app_user_id=_account().id,
        party_id=person.id,
        hired_on=hired_on,
    )


def _vollzeit_contract(app_user, employee, valid_from=date(2024, 1, 1), **kwargs):
    return service.create_contract(
        app_user.id,
        employee_id=employee.id,
        valid_from=valid_from,
        hours=VOLLZEIT,
        vacation_days_per_year=30,
        **kwargs,
    )


# --- Personalsatz ---------------------------------------------------------


@pytest.mark.django_db
def test_personalsatz_bekommt_nummer_von_der_db(app_user):
    employee = _employee(app_user)
    assert employee.employee_number.startswith("MA-")
    assert len(employee.employee_number) == 8  # MA- + 5 Stellen
    assert employee.status == "AKTIV"


@pytest.mark.django_db
def test_ein_konto_traegt_nur_einen_personalsatz(app_user):
    person_a = _person(app_user, "Anna", "Muster")
    person_b = _person(app_user, "Bert", "Beispiel")
    account = _account()
    service.create_employee(
        app_user.id, app_user_id=account.id, party_id=person_a.id, hired_on=date(2024, 1, 1)
    )
    with pytest.raises(ValueError, match="bereits ein Personalsatz"):
        service.create_employee(
            app_user.id,
            app_user_id=account.id,
            party_id=person_b.id,
            hired_on=date(2024, 1, 1),
        )


@pytest.mark.django_db
def test_austritt_verlangt_austrittsdatum(app_user):
    employee = _employee(app_user)
    with pytest.raises(ValueError, match="Austrittsdatum"):
        service.set_employee_status(
            app_user.id, employee_id=employee.id, status="AUSGETRETEN"
        )


@pytest.mark.django_db
def test_austritt_ist_final(app_user):
    employee = _employee(app_user)
    service.set_employee_status(
        app_user.id,
        employee_id=employee.id,
        status="AUSGETRETEN",
        left_on=date(2024, 6, 30),
    )
    with pytest.raises(ValueError, match="nicht zulässig"):
        service.set_employee_status(app_user.id, employee_id=employee.id, status="AKTIV")


@pytest.mark.django_db
def test_db_erzwingt_finalen_austritt_auch_am_service_vorbei(app_user):
    """Der Trigger hr.enforce_employee_status hält, wenn man den Service umgeht."""
    employee = _employee(app_user)
    service.set_employee_status(
        app_user.id,
        employee_id=employee.id,
        status="AUSGETRETEN",
        left_on=date(2024, 6, 30),
    )
    with pytest.raises(ProgrammingError, match="finaler Status"):
        with business_transaction(app_user.id):
            Employee.objects.filter(id=employee.id).update(status="AKTIV", left_on=None)


@pytest.mark.django_db
def test_personalsatz_ohne_person_scheitert(app_user):
    """party_id muss auf identity.person zeigen — der FK lässt nichts anderes zu."""
    with pytest.raises(IntegrityError):
        with business_transaction(app_user.id):
            Employee.objects.create(
                id=uuid.uuid4(),
                app_user_id=_account().id,
                party_id=uuid.uuid4(),
                status="AKTIV",
                hired_on=date(2024, 1, 1),
                created_by_id=app_user.id,
            )


# --- Arbeitsvertrag -------------------------------------------------------


@pytest.mark.django_db
def test_vertrag_ohne_arbeitstag_scheitert(app_user):
    employee = _employee(app_user)
    with pytest.raises(ValueError, match="mindestens einen Arbeitstag"):
        service.create_contract(
            app_user.id,
            employee_id=employee.id,
            valid_from=date(2024, 1, 1),
            hours={},
            vacation_days_per_year=30,
        )


@pytest.mark.django_db
def test_folgevertrag_schliesst_vorgaenger_am_vortag(app_user):
    employee = _employee(app_user)
    erster = _vollzeit_contract(app_user, employee, valid_from=date(2024, 1, 1))
    assert erster.valid_to is None

    zweiter = service.create_contract(
        app_user.id,
        employee_id=employee.id,
        valid_from=date(2024, 7, 1),
        hours={"hours_monday": 8, "hours_tuesday": 8, "hours_wednesday": 8},
        vacation_days_per_year=18,
    )
    erster.refresh_from_db()
    assert erster.valid_to == date(2024, 6, 30)
    assert zweiter.valid_from == date(2024, 7, 1)


@pytest.mark.django_db
def test_rueckwirkender_vertrag_wird_abgelehnt(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee, valid_from=date(2024, 7, 1))
    with pytest.raises(ValueError, match="rückwirkende Verträge"):
        _vollzeit_contract(app_user, employee, valid_from=date(2024, 7, 1))


@pytest.mark.django_db
def test_vertragsbeginn_vor_eintritt_wird_abgelehnt(app_user):
    employee = _employee(app_user, hired_on=date(2024, 3, 1))
    with pytest.raises(ValueError, match="vor dem Eintrittsdatum"):
        _vollzeit_contract(app_user, employee, valid_from=date(2024, 1, 1))


@pytest.mark.django_db
def test_sollstunden_sind_unveraenderlich(app_user):
    """Kein rückwirkendes Überschreiben — der DB-Trigger erzwingt es physisch."""
    employee = _employee(app_user)
    contract = _vollzeit_contract(app_user, employee)
    with pytest.raises(ProgrammingError, match="neuen Vertrag"):
        with business_transaction(app_user.id):
            EmploymentContract.objects.filter(id=contract.id).update(hours_monday=4)


@pytest.mark.django_db
def test_kuendigung_ist_begruendungspflichtig(app_user):
    employee = _employee(app_user)
    contract = _vollzeit_contract(app_user, employee)
    with pytest.raises(ValueError, match="begründungspflichtig"):
        service.terminate_contract(
            app_user.id, contract_id=contract.id, valid_to=date(2024, 12, 31), reason=" "
        )


@pytest.mark.django_db
def test_kuendigung_setzt_ende_und_grund(app_user):
    employee = _employee(app_user)
    contract = _vollzeit_contract(app_user, employee)
    gekuendigt = service.terminate_contract(
        app_user.id,
        contract_id=contract.id,
        valid_to=date(2024, 12, 31),
        reason="Eigenkündigung",
    )
    assert gekuendigt.status == "GEKUENDIGT"
    assert gekuendigt.valid_to == date(2024, 12, 31)
    assert gekuendigt.termination_reason == "Eigenkündigung"


@pytest.mark.django_db
def test_neuer_vertrag_nach_gekuendigtem_vorgaenger_wird_abgelehnt(app_user):
    """Ein gekündigter Vertrag darf nicht still gekürzt werden."""
    employee = _employee(app_user)
    contract = _vollzeit_contract(app_user, employee, valid_from=date(2024, 1, 1))
    service.terminate_contract(
        app_user.id, contract_id=contract.id, valid_to=date(2024, 12, 31), reason="Ende"
    )
    with pytest.raises(ValueError, match="gekündigt"):
        _vollzeit_contract(app_user, employee, valid_from=date(2024, 6, 1))


@pytest.mark.django_db
def test_db_erzwingt_ueberlappungsfreiheit_der_vertraege(app_user):
    """EXCLUDE-Constraint, auch wenn der Service umgangen wird."""
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee, valid_from=date(2024, 1, 1))
    with pytest.raises(IntegrityError):
        with business_transaction(app_user.id):
            EmploymentContract.objects.create(
                id=uuid.uuid4(),
                employee_id=employee.id,
                valid_from=date(2024, 6, 1),
                vacation_days_per_year=Decimal("30"),
                status="AKTIV",
                created_by_id=app_user.id,
                hours_monday=Decimal("8"),
                hours_tuesday=Decimal("0"),
                hours_wednesday=Decimal("0"),
                hours_thursday=Decimal("0"),
                hours_friday=Decimal("0"),
                hours_saturday=Decimal("0"),
                hours_sunday=Decimal("0"),
            )


# --- Abwesenheit: Tageberechnung -----------------------------------------


@pytest.mark.django_db
def test_wochenende_zaehlt_nicht_als_urlaubstag(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    # Mo 2024-06-03 bis So 2024-06-09 → 5 Arbeitstage
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 9),
    )
    assert absence.days_count == Decimal("5.00")


@pytest.mark.django_db
def test_halber_starttag_zieht_einen_halben_tag_ab(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),  # Montag
        end_date=date(2024, 6, 5),  # Mittwoch
        half_day_start=True,
    )
    assert absence.days_count == Decimal("2.50")


@pytest.mark.django_db
def test_eintaegiger_halber_urlaub(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 3),
        half_day_start=True,
    )
    assert absence.days_count == Decimal("0.50")


@pytest.mark.django_db
def test_teilzeitvertrag_zaehlt_nur_seine_arbeitstage(app_user):
    """Mi/Do/Fr frei → eine ganze Woche Urlaub kostet nur 2 Tage."""
    employee = _employee(app_user)
    service.create_contract(
        app_user.id,
        employee_id=employee.id,
        valid_from=date(2024, 1, 1),
        hours={"hours_monday": 8, "hours_tuesday": 8},
        vacation_days_per_year=12,
    )
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 7),
    )
    assert absence.days_count == Decimal("2.00")


@pytest.mark.django_db
def test_zeitraum_ohne_arbeitstag_wird_abgelehnt(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    with pytest.raises(ValueError, match="keinen Arbeitstag"):
        service.create_absence(
            app_user.id,
            employee_id=employee.id,
            absence_type="URLAUB",
            start_date=date(2024, 6, 8),  # Samstag
            end_date=date(2024, 6, 9),  # Sonntag
        )


@pytest.mark.django_db
def test_abwesenheit_ohne_vertrag_wird_abgelehnt(app_user):
    employee = _employee(app_user)
    with pytest.raises(ValueError, match="keinen Arbeitstag"):
        service.create_absence(
            app_user.id,
            employee_id=employee.id,
            absence_type="URLAUB",
            start_date=date(2024, 6, 3),
            end_date=date(2024, 6, 5),
        )


@pytest.mark.django_db
def test_vertragswechsel_im_zeitraum_wird_taggenau_gerechnet(app_user):
    """Erste Woche Vollzeit (5 Tage), ab Mi nur noch Mo/Di → Do/Fr zählen nicht."""
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee, valid_from=date(2024, 1, 1))
    service.create_contract(
        app_user.id,
        employee_id=employee.id,
        valid_from=date(2024, 6, 5),  # Mittwoch
        hours={"hours_monday": 8, "hours_tuesday": 8},
        vacation_days_per_year=12,
    )
    # Mo 03. + Di 04. (Vollzeit) + Mi 05.–Fr 07. (Teilzeit, keine Arbeitstage)
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 7),
    )
    assert absence.days_count == Decimal("2.00")


# --- Abwesenheit: Statusautomat -------------------------------------------


@pytest.mark.django_db
def test_abwesenheit_startet_als_entwurf(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 5),
    )
    assert absence.status == "ENTWURF"


@pytest.mark.django_db
def test_db_erzwingt_entwurf_als_startstatus(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    with pytest.raises(ProgrammingError, match="Status ENTWURF beginnen"):
        with business_transaction(app_user.id):
            Absence.objects.create(
                id=uuid.uuid4(),
                employee_id=employee.id,
                absence_type="URLAUB",
                start_date=date(2024, 6, 3),
                end_date=date(2024, 6, 5),
                days_count=Decimal("3"),
                status="GENEHMIGT",
                created_by_id=app_user.id,
            )


@pytest.mark.django_db
def test_genehmigung_nur_aus_eingereicht(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 5),
    )
    with pytest.raises(ValueError, match="nicht zulässig"):
        service.approve_absence(app_user.id, absence_id=absence.id)


@pytest.mark.django_db
def test_genehmigung_protokolliert_entscheider(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 5),
    )
    service.submit_absence(app_user.id, absence_id=absence.id)
    genehmigt = service.approve_absence(app_user.id, absence_id=absence.id)
    assert genehmigt.status == "GENEHMIGT"
    assert genehmigt.decided_by_id == app_user.id
    assert genehmigt.decided_at is not None


@pytest.mark.django_db
def test_ablehnung_ist_begruendungspflichtig(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 5),
    )
    service.submit_absence(app_user.id, absence_id=absence.id)
    with pytest.raises(ValueError, match="begründungspflichtig"):
        service.reject_absence(app_user.id, absence_id=absence.id, note="  ")


@pytest.mark.django_db
def test_ueberlappende_abwesenheit_wird_abgelehnt(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 7),
    )
    with pytest.raises(ValueError, match="bereits eine Abwesenheit"):
        service.create_absence(
            app_user.id,
            employee_id=employee.id,
            absence_type="KRANKHEIT",
            start_date=date(2024, 6, 5),
            end_date=date(2024, 6, 12),
        )


@pytest.mark.django_db
def test_zurueckgezogene_abwesenheit_gibt_zeitraum_frei(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 7),
    )
    service.withdraw_absence(app_user.id, absence_id=absence.id)
    # derselbe Zeitraum ist wieder frei
    neu = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="KRANKHEIT",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 7),
    )
    assert neu.status == "ENTWURF"


# --- Urlaubskonto ---------------------------------------------------------


@pytest.mark.django_db
def test_urlaubskonto_leitet_verbrauch_aus_genehmigten_urlauben_ab(app_user):
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    service.set_vacation_budget(
        app_user.id,
        employee_id=employee.id,
        year=2024,
        entitlement_days=30,
        carryover_days=5,
    )
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 7),
    )
    service.submit_absence(app_user.id, absence_id=absence.id)
    service.approve_absence(app_user.id, absence_id=absence.id)

    konto = service.vacation_account(employee.id, 2024)
    assert konto["total_days"] == Decimal("35.00")
    assert konto["used_days"] == Decimal("5.00")
    assert konto["remaining_days"] == Decimal("30.00")


@pytest.mark.django_db
def test_nur_genehmigter_urlaub_zaehlt_als_verbrauch(app_user):
    """Krankheit und noch nicht genehmigte Anträge belasten das Konto nicht."""
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    service.set_vacation_budget(
        app_user.id, employee_id=employee.id, year=2024, entitlement_days=30
    )
    # eingereicht, aber nicht genehmigt
    offen = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 7),
    )
    service.submit_absence(app_user.id, absence_id=offen.id)
    # genehmigte Krankheit
    krank = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="KRANKHEIT",
        start_date=date(2024, 6, 10),
        end_date=date(2024, 6, 12),
    )
    service.submit_absence(app_user.id, absence_id=krank.id)
    service.approve_absence(app_user.id, absence_id=krank.id)

    konto = service.vacation_account(employee.id, 2024)
    assert konto["used_days"] == Decimal("0")
    assert konto["remaining_days"] == Decimal("30.00")


@pytest.mark.django_db
def test_anpassung_ist_begruendungspflichtig(app_user):
    employee = _employee(app_user)
    with pytest.raises(ValueError, match="begründungspflichtig"):
        service.set_vacation_budget(
            app_user.id,
            employee_id=employee.id,
            year=2024,
            entitlement_days=30,
            adjustment_days=-2,
        )


@pytest.mark.django_db
def test_negative_anpassung_mit_begruendung_reduziert_das_konto(app_user):
    employee = _employee(app_user)
    service.set_vacation_budget(
        app_user.id,
        employee_id=employee.id,
        year=2024,
        entitlement_days=30,
        adjustment_days=-2,
        adjustment_reason="Unterjähriger Eintritt",
    )
    konto = service.vacation_account(employee.id, 2024)
    assert konto["total_days"] == Decimal("28.00")


@pytest.mark.django_db
def test_urlaubskonto_ist_idempotent(app_user):
    employee = _employee(app_user)
    service.set_vacation_budget(
        app_user.id, employee_id=employee.id, year=2024, entitlement_days=30
    )
    service.set_vacation_budget(
        app_user.id, employee_id=employee.id, year=2024, entitlement_days=28
    )
    konto = service.vacation_account(employee.id, 2024)
    assert konto["entitlement_days"] == Decimal("28.00")


@pytest.mark.django_db
def test_urlaubskonto_ohne_datensatz_liefert_nullen(app_user):
    employee = _employee(app_user)
    konto = service.vacation_account(employee.id, 2024)
    assert konto["total_days"] == Decimal("0")
    assert konto["remaining_days"] == Decimal("0")


# --- Regressionen aus dem Review -----------------------------------------


@pytest.mark.django_db
def test_kuendigung_hinter_folgevertrag_wird_abgelehnt(app_user):
    """Review-Blocker: ein Ende hinter dem Folgevertrag verletzte den EXCLUDE
    und erzeugte einen IntegrityError (500) statt eines fachlichen 422."""
    employee = _employee(app_user)
    erster = _vollzeit_contract(app_user, employee, valid_from=date(2024, 1, 1))
    # Folgevertrag ab Juli kürzt den ersten automatisch auf 30.06.
    service.create_contract(
        app_user.id,
        employee_id=employee.id,
        valid_from=date(2024, 7, 1),
        hours={"hours_monday": 8, "hours_tuesday": 8},
        vacation_days_per_year=12,
    )
    with pytest.raises(ValueError, match="Folgevertrag beginnt"):
        service.terminate_contract(
            app_user.id,
            contract_id=erster.id,
            valid_to=date(2024, 8, 1),
            reason="zu spätes Ende",
        )


@pytest.mark.django_db
def test_wirkungsloser_halber_randtag_wird_nicht_gespeichert(app_user):
    """Ein halber Tag auf einem Nicht-Arbeitstag (Sonntag) darf kein Flag setzen."""
    employee = _employee(app_user)
    _vollzeit_contract(app_user, employee)
    # So 2024-06-02 (frei) bis Fr 2024-06-07 → 5 Arbeitstage, halber Starttag wirkungslos
    absence = service.create_absence(
        app_user.id,
        employee_id=employee.id,
        absence_type="URLAUB",
        start_date=date(2024, 6, 2),
        end_date=date(2024, 6, 7),
        half_day_start=True,
    )
    assert absence.days_count == Decimal("5.00")
    assert absence.half_day_start is False
