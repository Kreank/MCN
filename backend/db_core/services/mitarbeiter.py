"""Personal-Service: hr.employee, hr.employment_contract, hr.absence,
hr.vacation_budget (Migration 0019).

Wie die übrigen Services laufen alle Writes über business_transaction; die
Personalnummer (MA-…) vergibt die DB (db_default → refresh_from_db).

Zwei Regeln tragen die Domäne und werden hier vorab geprüft (klarer ValueError
→ 422), von der DB aber zusätzlich physisch erzwungen:

* **Verträge überlappen nicht.** Ein Mitarbeiter hat zu jedem Zeitpunkt höchstens
  einen Vertrag (EXCLUDE-Constraint). Ein Folgevertrag schließt den laufenden
  automatisch am Vortag ab, statt den Anwender in den Constraint laufen zu lassen.
* **Kein rückwirkendes Überschreiben.** Beginn, Sollstunden, Urlaubsanspruch und
  Lohngruppe eines bestehenden Vertrags sind unveränderlich (Trigger); eine
  Arbeitszeitänderung erzeugt einen neuen Vertrag.

Die angerechneten Abwesenheitstage (days_count) berechnet der Service aus dem
Sollstunden-Raster des jeweils gültigen Vertrags — Tage ohne Soll (Wochenende,
0-Stunden-Tage) zählen nicht. Der Client liefert days_count nie selbst.

Bekannte Lücke (bewusst): ein Feiertagskalender existiert im Schema nicht, daher
zählen gesetzliche Feiertage derzeit als Arbeitstage, wenn der Vertrag für den
Wochentag ein Soll ausweist. Ebenso ist der unterjährige Vertragsbeginn nicht
automatisch in den Urlaubsanspruch eingerechnet (Hero verhält sich genauso; die
manuelle Anpassung am Urlaubskonto ist dafür vorgesehen).
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Absence,
    AppUser,
    Employee,
    EmploymentContract,
    Person,
    VacationBudget,
    WageGroup,
)
from db_core.services._validation import ensure_exists

EMPLOYEE_STATUS = ("AKTIV", "INAKTIV", "AUSGETRETEN")
ABSENCE_TYPES = (
    "URLAUB",
    "KRANKHEIT",
    "ELTERNZEIT",
    "SONDERURLAUB",
    "UNBEZAHLT",
    "FORTBILDUNG",
)

# Erlaubte Statusübergänge → {Zielstatus}. AUSGETRETEN ist final; ein
# Wiedereintritt ist fachlich ein neuer Personalsatz. Spiegelt den DB-Trigger
# hr.enforce_employee_status.
EMPLOYEE_TRANSITIONS = {
    "AKTIV": {"INAKTIV", "AUSGETRETEN"},
    "INAKTIV": {"AKTIV", "AUSGETRETEN"},
    "AUSGETRETEN": set(),
}

# Spiegelt hr.enforce_absence_status.
ABSENCE_TRANSITIONS = {
    "ENTWURF": {"EINGEREICHT", "ZURUECKGEZOGEN"},
    "EINGEREICHT": {"GENEHMIGT", "ABGELEHNT", "ZURUECKGEZOGEN"},
    "GENEHMIGT": set(),
    "ABGELEHNT": set(),
    "ZURUECKGEZOGEN": set(),
}

# Wochentag (date.weekday(): Mo=0) → Spalte des Sollstunden-Rasters.
_WEEKDAY_FIELDS = (
    "hours_monday",
    "hours_tuesday",
    "hours_wednesday",
    "hours_thursday",
    "hours_friday",
    "hours_saturday",
    "hours_sunday",
)

_HALF = Decimal("0.5")
_ONE = Decimal("1")


def _get_employee(employee_id):
    employee = Employee.objects.filter(id=employee_id).first()
    if employee is None:
        raise ValueError(f"Mitarbeiter {employee_id} existiert nicht")
    return employee


def _contract_on(contracts, day):
    """Der an einem Tag gültige Vertrag aus einer vorgeladenen Liste (oder None).

    Überlappungsfreiheit garantiert der EXCLUDE-Constraint, daher ist das
    Ergebnis eindeutig.
    """
    for contract in contracts:
        if contract.valid_from <= day and (
            contract.valid_to is None or contract.valid_to >= day
        ):
            return contract
    return None


def _is_working_day(contract, day):
    """Weist der Vertrag für den Wochentag dieses Tages ein Soll > 0 aus?"""
    if contract is None:
        return False
    hours = getattr(contract, _WEEKDAY_FIELDS[day.weekday()])
    return hours > 0


def compute_absence_days(employee_id, start_date, end_date, half_day_start, half_day_end):
    """Angerechnete Arbeitstage im Zeitraum, halbe Randtage berücksichtigt.

    Tage ohne gültigen Vertrag oder mit Soll 0 zählen nicht mit. Ein halber
    Randtag zieht 0,5 ab — aber nur, wenn dieser Randtag überhaupt ein
    Arbeitstag ist.
    """
    # Die im Zeitraum überhaupt relevanten Verträge einmal laden (statt je Tag).
    contracts = list(
        EmploymentContract.objects.filter(
            employee_id=employee_id, valid_from__lte=end_date
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=start_date))
    )

    days = Decimal("0")
    day = start_date
    while day <= end_date:
        if _is_working_day(_contract_on(contracts, day), day):
            days += _ONE
        day += timedelta(days=1)

    if days == 0:
        return days

    if half_day_start and _is_working_day(_contract_on(contracts, start_date), start_date):
        days -= _HALF
    if half_day_end and _is_working_day(_contract_on(contracts, end_date), end_date):
        days -= _HALF
    return days


# --- Personalsatz ---------------------------------------------------------


def create_employee(
    actor_app_user_id,
    *,
    app_user_id,
    party_id,
    hired_on,
    wage_group_id=None,
    notes=None,
):
    """Legt einen Personalsatz an. party_id muss eine identity.person sein (FK)."""
    ensure_exists(AppUser, app_user_id, "Benutzerkonto")
    ensure_exists(Person, party_id, "Person")
    ensure_exists(WageGroup, wage_group_id, "Lohngruppe")
    if Employee.objects.filter(app_user_id=app_user_id).exists():
        raise ValueError("Für dieses Benutzerkonto existiert bereits ein Personalsatz")
    if Employee.objects.filter(party_id=party_id).exists():
        raise ValueError("Für diese Person existiert bereits ein Personalsatz")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            employee = Employee.objects.create(
                id=uuid.uuid4(),
                app_user_id=app_user_id,
                party_id=party_id,
                wage_group_id=wage_group_id,
                status="AKTIV",
                hired_on=hired_on,
                notes=notes,
                created_by_id=actor_app_user_id,
            )
    employee.refresh_from_db()
    return employee


def set_employee_status(actor_app_user_id, *, employee_id, status, left_on=None):
    """Statuswechsel. AUSGETRETEN verlangt ein Austrittsdatum (DB-CHECK)."""
    employee = _get_employee(employee_id)
    if status not in EMPLOYEE_STATUS:
        raise ValueError(f"Unbekannter Status: {status}")
    if status not in EMPLOYEE_TRANSITIONS[employee.status]:
        raise ValueError(
            f"Statuswechsel {employee.status} -> {status} ist nicht zulässig"
        )
    if status == "AUSGETRETEN":
        if left_on is None:
            raise ValueError("Austritt erfordert ein Austrittsdatum")
        if left_on < employee.hired_on:
            raise ValueError("Das Austrittsdatum liegt vor dem Eintrittsdatum")
    elif left_on is not None:
        raise ValueError("Ein Austrittsdatum ist nur beim Austritt zulässig")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            Employee.objects.filter(id=employee_id).update(
                status=status, left_on=left_on
            )
    employee.refresh_from_db()
    return employee


# --- Arbeitsvertrag -------------------------------------------------------


def create_contract(
    actor_app_user_id,
    *,
    employee_id,
    valid_from,
    hours,
    vacation_days_per_year,
    valid_to=None,
    wage_group_id=None,
    notes=None,
):
    """Legt einen Vertrag an; `hours` ist ein Dict Wochentag-Feld → Stunden.

    Ein noch laufender Vorgängervertrag wird am Vortag des neuen Beginns
    beendet (Hero: Arbeitszeitänderung = neuer Vertrag). Rückwirkende Verträge,
    die einen bereits beendeten Zeitraum überlappen, lehnt der Service ab.
    """
    employee = _get_employee(employee_id)
    ensure_exists(WageGroup, wage_group_id, "Lohngruppe")
    if employee.status == "AUSGETRETEN":
        raise ValueError("Für einen ausgetretenen Mitarbeiter kann kein Vertrag angelegt werden")

    unknown = set(hours) - set(_WEEKDAY_FIELDS)
    if unknown:
        raise ValueError(f"Unbekannte Sollstunden-Felder: {sorted(unknown)}")

    values = {field: Decimal(str(hours.get(field, 0))) for field in _WEEKDAY_FIELDS}
    for field, value in values.items():
        if value < 0 or value > 24:
            raise ValueError(f"{field}: Sollstunden müssen zwischen 0 und 24 liegen")
    if sum(values.values()) <= 0:
        raise ValueError("Der Vertrag muss mindestens einen Arbeitstag ausweisen")

    vacation_days = Decimal(str(vacation_days_per_year))
    if vacation_days < 0 or vacation_days > 366:
        raise ValueError("Urlaubsanspruch muss zwischen 0 und 366 Tagen liegen")
    if valid_to is not None and valid_to < valid_from:
        raise ValueError("Das Vertragsende liegt vor dem Vertragsbeginn")
    if valid_from < employee.hired_on:
        raise ValueError("Der Vertragsbeginn liegt vor dem Eintrittsdatum")

    # Vorgänger, der in den neuen Zeitraum hineinragt.
    predecessor = (
        EmploymentContract.objects.filter(employee_id=employee_id, valid_from__lt=valid_from)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=valid_from))
        .order_by("-valid_from")
        .first()
    )
    # Verträge, die am oder nach dem neuen Beginn liegen, blockieren.
    if EmploymentContract.objects.filter(
        employee_id=employee_id, valid_from__gte=valid_from
    ).exists():
        raise ValueError(
            "Es existiert bereits ein Vertrag ab diesem Datum oder später; "
            "rückwirkende Verträge sind nicht zulässig"
        )

    # Einen bereits gekündigten Vertrag still zu kürzen wäre eine rückwirkende
    # Änderung an einer abgeschlossenen Vereinbarung — das lehnen wir ab.
    if predecessor is not None and predecessor.status == "GEKUENDIGT":
        raise ValueError(
            f"Der Vorgängervertrag ist zum {predecessor.valid_to} gekündigt; "
            "der neue Vertrag darf frühestens am Folgetag beginnen"
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            if predecessor is not None:
                EmploymentContract.objects.filter(id=predecessor.id).update(
                    valid_to=valid_from - timedelta(days=1)
                )
            contract = EmploymentContract.objects.create(
                id=uuid.uuid4(),
                employee_id=employee_id,
                valid_from=valid_from,
                valid_to=valid_to,
                vacation_days_per_year=vacation_days,
                wage_group_id=wage_group_id,
                status="AKTIV",
                notes=notes,
                created_by_id=actor_app_user_id,
                **values,
            )
    contract.refresh_from_db()
    return contract


def terminate_contract(actor_app_user_id, *, contract_id, valid_to, reason):
    """Kündigt einen Vertrag zum Datum `valid_to` (begründungspflichtig)."""
    contract = EmploymentContract.objects.filter(id=contract_id).first()
    if contract is None:
        raise ValueError(f"Vertrag {contract_id} existiert nicht")
    if contract.status == "GEKUENDIGT":
        raise ValueError("Der Vertrag ist bereits gekündigt")
    if not (reason or "").strip():
        raise ValueError("Eine Kündigung ist begründungspflichtig")
    if valid_to < contract.valid_from:
        raise ValueError("Das Vertragsende liegt vor dem Vertragsbeginn")

    # Ein Ende hinter dem Beginn des Folgevertrags würde den EXCLUDE-Constraint
    # verletzen (IntegrityError → 500). Vorab prüfen, damit daraus ein 422 wird.
    successor = (
        EmploymentContract.objects.filter(
            employee_id=contract.employee_id, valid_from__gt=contract.valid_from
        )
        .order_by("valid_from")
        .first()
    )
    if successor is not None and valid_to >= successor.valid_from:
        raise ValueError(
            f"Ein Folgevertrag beginnt am {successor.valid_from}; das Vertragsende "
            "muss davor liegen"
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            EmploymentContract.objects.filter(id=contract_id).update(
                status="GEKUENDIGT", valid_to=valid_to, termination_reason=reason.strip()
            )
    contract.refresh_from_db()
    return contract


# --- Abwesenheit ----------------------------------------------------------


def create_absence(
    actor_app_user_id,
    *,
    employee_id,
    absence_type,
    start_date,
    end_date,
    half_day_start=False,
    half_day_end=False,
    reason=None,
):
    """Legt einen Abwesenheitsantrag im Status ENTWURF an (Trigger erzwingt das).

    days_count wird berechnet, nicht übergeben.
    """
    _get_employee(employee_id)
    if absence_type not in ABSENCE_TYPES:
        raise ValueError(f"Unbekannte Abwesenheitsart: {absence_type}")
    if end_date < start_date:
        raise ValueError("Das Enddatum liegt vor dem Startdatum")
    if start_date == end_date and half_day_end:
        raise ValueError(
            "Ein eintägiger Zeitraum kann nur eine Hälfte sein — halber Starttag genügt"
        )

    days = compute_absence_days(
        employee_id, start_date, end_date, half_day_start, half_day_end
    )
    if days <= 0:
        raise ValueError(
            "Der Zeitraum enthält keinen Arbeitstag laut gültigem Vertrag "
            "(fehlender Vertrag, Wochenende oder 0-Stunden-Tage)"
        )

    # Ein halber Randtag, der gar kein Arbeitstag ist, bleibt wirkungslos —
    # dann darf das Flag auch nicht gespeichert werden, sonst zeigt das UI ein
    # „½" ohne Entsprechung in days_count.
    contracts = list(
        EmploymentContract.objects.filter(
            employee_id=employee_id, valid_from__lte=end_date
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=start_date))
    )
    half_day_start = half_day_start and _is_working_day(
        _contract_on(contracts, start_date), start_date
    )
    half_day_end = half_day_end and _is_working_day(
        _contract_on(contracts, end_date), end_date
    )

    overlapping = Absence.objects.filter(
        employee_id=employee_id,
        status__in=("ENTWURF", "EINGEREICHT", "GENEHMIGT"),
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).exists()
    if overlapping:
        raise ValueError("Für diesen Zeitraum existiert bereits eine Abwesenheit")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            absence = Absence.objects.create(
                id=uuid.uuid4(),
                employee_id=employee_id,
                absence_type=absence_type,
                start_date=start_date,
                end_date=end_date,
                half_day_start=half_day_start,
                half_day_end=half_day_end,
                days_count=days,
                status="ENTWURF",
                reason=reason,
                created_by_id=actor_app_user_id,
            )
    absence.refresh_from_db()
    return absence


def _advance_absence(actor_app_user_id, absence_id, target, **fields):
    absence = Absence.objects.filter(id=absence_id).first()
    if absence is None:
        raise ValueError(f"Abwesenheit {absence_id} existiert nicht")
    if target not in ABSENCE_TRANSITIONS[absence.status]:
        raise ValueError(
            f"Statuswechsel {absence.status} -> {target} ist nicht zulässig"
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            Absence.objects.filter(id=absence_id).update(status=target, **fields)
    absence.refresh_from_db()
    return absence


def submit_absence(actor_app_user_id, *, absence_id):
    return _advance_absence(actor_app_user_id, absence_id, "EINGEREICHT")


def withdraw_absence(actor_app_user_id, *, absence_id):
    return _advance_absence(actor_app_user_id, absence_id, "ZURUECKGEZOGEN")


def approve_absence(actor_app_user_id, *, absence_id, note=None):
    """Genehmigt einen eingereichten Antrag. Der Entscheider wird protokolliert."""
    from django.utils import timezone

    return _advance_absence(
        actor_app_user_id,
        absence_id,
        "GENEHMIGT",
        decided_by_id=actor_app_user_id,
        decided_at=timezone.now(),
        decision_note=note,
    )


def reject_absence(actor_app_user_id, *, absence_id, note):
    """Lehnt einen eingereichten Antrag ab — begründungspflichtig (DB-CHECK)."""
    from django.utils import timezone

    if not (note or "").strip():
        raise ValueError("Eine Ablehnung ist begründungspflichtig")
    return _advance_absence(
        actor_app_user_id,
        absence_id,
        "ABGELEHNT",
        decided_by_id=actor_app_user_id,
        decided_at=timezone.now(),
        decision_note=note.strip(),
    )


# --- Urlaubskonto ---------------------------------------------------------


def set_vacation_budget(
    actor_app_user_id,
    *,
    employee_id,
    year,
    entitlement_days,
    carryover_days=0,
    adjustment_days=0,
    adjustment_reason=None,
):
    """Legt das Urlaubskonto eines Jahres an oder aktualisiert es (idempotent)."""
    _get_employee(employee_id)
    if not 2000 <= year <= 2100:
        raise ValueError("Jahr muss zwischen 2000 und 2100 liegen")

    entitlement = Decimal(str(entitlement_days))
    carryover = Decimal(str(carryover_days))
    adjustment = Decimal(str(adjustment_days))
    if entitlement < 0:
        raise ValueError("Der Urlaubsanspruch darf nicht negativ sein")
    if carryover < 0:
        raise ValueError("Der Übertrag darf nicht negativ sein")
    if adjustment != 0 and not (adjustment_reason or "").strip():
        raise ValueError("Eine Anpassung des Urlaubskontos ist begründungspflichtig")

    reason = (adjustment_reason or "").strip() or None
    existing = VacationBudget.objects.filter(employee_id=employee_id, year=year).first()

    with as_business_error():
        with business_transaction(actor_app_user_id):
            if existing is None:
                budget = VacationBudget.objects.create(
                    id=uuid.uuid4(),
                    employee_id=employee_id,
                    year=year,
                    entitlement_days=entitlement,
                    carryover_days=carryover,
                    adjustment_days=adjustment,
                    adjustment_reason=reason,
                    created_by_id=actor_app_user_id,
                )
            else:
                VacationBudget.objects.filter(id=existing.id).update(
                    entitlement_days=entitlement,
                    carryover_days=carryover,
                    adjustment_days=adjustment,
                    adjustment_reason=reason,
                )
                budget = existing
    budget.refresh_from_db()
    return budget


def vacation_account(employee_id, year):
    """Urlaubskonto eines Jahres inklusive abgeleitetem Verbrauch und Rest.

    Verbrauch = Summe der days_count aller GENEHMIGTEN URLAUB-Abwesenheiten,
    die im Jahr *beginnen*. (Jahresübergreifende Anträge werden dem Startjahr
    zugerechnet — eine tagegenaue Aufteilung wäre erst mit Feiertagskalender
    sinnvoll.)
    """
    budget = VacationBudget.objects.filter(employee_id=employee_id, year=year).first()
    entitlement = budget.entitlement_days if budget else Decimal("0")
    carryover = budget.carryover_days if budget else Decimal("0")
    adjustment = budget.adjustment_days if budget else Decimal("0")

    used = Decimal("0")
    absences = Absence.objects.filter(
        employee_id=employee_id,
        absence_type="URLAUB",
        status="GENEHMIGT",
        start_date__year=year,
    )
    for absence in absences:
        used += absence.days_count

    total = entitlement + carryover + adjustment
    return {
        "year": year,
        "entitlement_days": entitlement,
        "carryover_days": carryover,
        "adjustment_days": adjustment,
        "adjustment_reason": budget.adjustment_reason if budget else None,
        "total_days": total,
        "used_days": used,
        "remaining_days": total - used,
    }
