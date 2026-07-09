"""Personal-API — Mitarbeiter, Arbeitsverträge, Abwesenheiten, Urlaubskonto (hr.*).

Lesen in der Dev-Phase ohne Auth; Anlegen, Statuswechsel und Genehmigungen
verlangen Django-Session + zugeordnetes app_user. Views dünn, rufen die
Service-Schicht.

Personendaten (Name, Anrede, Geburtsdatum) liegen in identity.person und werden
hier nur mitgelesen, nie geschrieben — dafür ist die Kontakte-API zuständig.
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Case, IntegerField, Q, Value, When
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from db_core.models import Absence, Employee, EmploymentContract
from db_core.services import mitarbeiter as mitarbeiter_service

router = Router()

EMPLOYEE_STATUS = ("AKTIV", "INAKTIV", "AUSGETRETEN")
ABSENCE_STATUS = ("ENTWURF", "EINGEREICHT", "GENEHMIGT", "ABGELEHNT", "ZURUECKGEZOGEN")


# --- Schemas ---------------------------------------------------------------

class WageGroupRefOut(Schema):
    id: UUID
    name: str
    hourly_rate: Decimal


class EmployeeOut(Schema):
    id: UUID
    employee_number: str
    first_name: str
    last_name: str
    display_name: str
    status: str
    hired_on: date
    left_on: date | None = None
    wage_group: WageGroupRefOut | None = None


class ContractOut(Schema):
    id: UUID
    valid_from: date
    valid_to: date | None = None
    status: str
    weekly_hours: Decimal
    hours_monday: Decimal
    hours_tuesday: Decimal
    hours_wednesday: Decimal
    hours_thursday: Decimal
    hours_friday: Decimal
    hours_saturday: Decimal
    hours_sunday: Decimal
    vacation_days_per_year: Decimal
    wage_group: WageGroupRefOut | None = None
    termination_reason: str | None = None
    notes: str | None = None
    is_current: bool


class AbsenceOut(Schema):
    id: UUID
    absence_type: str
    start_date: date
    end_date: date
    half_day_start: bool
    half_day_end: bool
    days_count: Decimal
    status: str
    reason: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None


class VacationAccountOut(Schema):
    year: int
    entitlement_days: Decimal
    carryover_days: Decimal
    adjustment_days: Decimal
    adjustment_reason: str | None = None
    total_days: Decimal
    used_days: Decimal
    remaining_days: Decimal


class EmployeeDetailOut(EmployeeOut):
    salutation: str | None = None
    birth_date: date | None = None
    notes: str | None = None
    created_at: datetime
    contracts: list[ContractOut]
    absences: list[AbsenceOut]
    vacation_account: VacationAccountOut


class EmployeeListOut(Schema):
    items: list[EmployeeOut]
    total: int
    page: int
    page_size: int


class EmployeeFilter(Schema):
    q: str | None = None
    status: str | None = None


class EmployeeIn(Schema):
    app_user_id: UUID
    party_id: UUID
    hired_on: date
    wage_group_id: UUID | None = None
    notes: str | None = None


class EmployeeStatusIn(Schema):
    status: str
    left_on: date | None = None


class ContractIn(Schema):
    valid_from: date
    vacation_days_per_year: Decimal
    hours_monday: Decimal = Decimal("0")
    hours_tuesday: Decimal = Decimal("0")
    hours_wednesday: Decimal = Decimal("0")
    hours_thursday: Decimal = Decimal("0")
    hours_friday: Decimal = Decimal("0")
    hours_saturday: Decimal = Decimal("0")
    hours_sunday: Decimal = Decimal("0")
    valid_to: date | None = None
    wage_group_id: UUID | None = None
    notes: str | None = None


class TerminateIn(Schema):
    valid_to: date
    reason: str


class AbsenceIn(Schema):
    absence_type: str
    start_date: date
    end_date: date
    half_day_start: bool = False
    half_day_end: bool = False
    reason: str | None = None


class DecisionIn(Schema):
    note: str | None = None


class VacationBudgetIn(Schema):
    year: int
    entitlement_days: Decimal
    carryover_days: Decimal = Decimal("0")
    adjustment_days: Decimal = Decimal("0")
    adjustment_reason: str | None = None


# --- Mapper ----------------------------------------------------------------

_HOUR_FIELDS = (
    "hours_monday",
    "hours_tuesday",
    "hours_wednesday",
    "hours_thursday",
    "hours_friday",
    "hours_saturday",
    "hours_sunday",
)


def _wage_group_ref(wage_group):
    if wage_group is None:
        return None
    return WageGroupRefOut(
        id=wage_group.id, name=wage_group.name, hourly_rate=wage_group.hourly_rate
    )


def _employee_out(employee):
    person = employee.party  # identity.person (OneToOne, PK = party_id)
    return EmployeeOut(
        id=employee.id,
        employee_number=employee.employee_number,
        first_name=person.first_name,
        last_name=person.last_name,
        display_name=f"{person.first_name} {person.last_name}",
        status=employee.status,
        hired_on=employee.hired_on,
        left_on=employee.left_on,
        wage_group=_wage_group_ref(employee.wage_group),
    )


def _contract_out(contract, today):
    """is_current: der Vertrag deckt den heutigen Tag ab."""
    is_current = contract.valid_from <= today and (
        contract.valid_to is None or contract.valid_to >= today
    )
    weekly = sum(getattr(contract, field) for field in _HOUR_FIELDS)
    return ContractOut(
        id=contract.id,
        valid_from=contract.valid_from,
        valid_to=contract.valid_to,
        status=contract.status,
        weekly_hours=weekly,
        vacation_days_per_year=contract.vacation_days_per_year,
        wage_group=_wage_group_ref(contract.wage_group),
        termination_reason=contract.termination_reason,
        notes=contract.notes,
        is_current=is_current,
        **{field: getattr(contract, field) for field in _HOUR_FIELDS},
    )


def _absence_out(absence):
    return AbsenceOut(
        id=absence.id,
        absence_type=absence.absence_type,
        start_date=absence.start_date,
        end_date=absence.end_date,
        half_day_start=absence.half_day_start,
        half_day_end=absence.half_day_end,
        days_count=absence.days_count,
        status=absence.status,
        reason=absence.reason,
        decided_at=absence.decided_at,
        decision_note=absence.decision_note,
    )


def _employee_qs():
    return Employee.objects.select_related("party", "wage_group")


# --- Lesende Endpoints -----------------------------------------------------

@router.get("/employees", response=EmployeeListOut)
def list_employees(
    request,
    filters: EmployeeFilter = Query(...),
    page: int = 1,
    page_size: int = 25,
):
    """Mitarbeiterliste mit Suche (Name/Personalnummer) und Statusfilter."""
    if filters.status and filters.status not in EMPLOYEE_STATUS:
        raise HttpError(422, f"Unbekannter Status: {filters.status}")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    qs = _employee_qs()
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.q:
        term = filters.q.strip()
        qs = qs.filter(
            Q(employee_number__icontains=term)
            | Q(party__first_name__icontains=term)
            | Q(party__last_name__icontains=term)
        )

    # Aktive zuerst, Ausgetretene zuletzt; innerhalb nach Nachname.
    qs = qs.annotate(
        status_rank=Case(
            When(status="AKTIV", then=Value(0)),
            When(status="INAKTIV", then=Value(1)),
            When(status="AUSGETRETEN", then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by("status_rank", "party__last_name", "party__first_name", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_employee_out(e) for e in qs[start:start + page_size]]
    return EmployeeListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/employees/{employee_id}", response=EmployeeDetailOut)
def get_employee(request, employee_id: UUID, year: int | None = None):
    """Mitarbeiter-Mappe: Stammdaten, Verträge, Abwesenheiten, Urlaubskonto."""
    employee = _employee_qs().filter(id=employee_id).first()
    if employee is None:
        raise HttpError(404, "Mitarbeiter nicht gefunden.")

    today = date.today()
    year = year or today.year

    contracts = (
        EmploymentContract.objects.select_related("wage_group")
        .filter(employee_id=employee_id)
        .order_by("-valid_from")
    )
    absences = Absence.objects.filter(employee_id=employee_id).order_by("-start_date")
    account = mitarbeiter_service.vacation_account(employee_id, year)

    person = employee.party
    base = _employee_out(employee).model_dump()
    return EmployeeDetailOut(
        **base,
        salutation=person.salutation,
        birth_date=person.birth_date,
        notes=employee.notes,
        created_at=employee.created_at,
        contracts=[_contract_out(c, today) for c in contracts],
        absences=[_absence_out(a) for a in absences],
        vacation_account=VacationAccountOut(**account),
    )


@router.get("/absences", response=list[AbsenceOut], auth=django_auth)
def list_absences(request, status: str | None = None, employee_id: UUID | None = None):
    """Abwesenheiten quer über alle Mitarbeiter (Genehmigungs-Eingang).

    Abweichend von den übrigen Lese-Endpunkten der Dev-Phase verlangt dieser
    eine Session: er gibt `absence_type` (u. a. KRANKHEIT) über den gesamten
    Personalbestand aus. Gesundheitsdaten sind eine besondere Kategorie nach
    DSGVO Art. 9 und gehören nicht in eine offene Leseschnittstelle.
    """
    if status and status not in ABSENCE_STATUS:
        raise HttpError(422, f"Unbekannter Status: {status}")
    qs = Absence.objects.all()
    if status:
        qs = qs.filter(status=status)
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    return [_absence_out(a) for a in qs.order_by("-start_date")[:200]]


# --- Schreibende Endpoints (Session-Auth Pflicht) --------------------------

def _actor_id(request):
    actor = getattr(request.user, "app_user_id", None)
    if actor is None:
        raise HttpError(
            403,
            "Dem Login-Konto ist kein security.app_user zugeordnet; "
            "fachliche Schreibvorgänge sind damit nicht möglich.",
        )
    return actor


def _reload_employee(employee_id):
    employee = _employee_qs().filter(id=employee_id).first()
    if employee is None:
        raise HttpError(404, "Mitarbeiter nicht gefunden.")
    return _employee_out(employee)


@router.post("/employees", response={201: EmployeeOut}, auth=django_auth)
def create_employee(request, payload: EmployeeIn):
    """Personalsatz anlegen (Status AKTIV)."""
    actor = _actor_id(request)
    try:
        employee = mitarbeiter_service.create_employee(
            actor,
            app_user_id=payload.app_user_id,
            party_id=payload.party_id,
            hired_on=payload.hired_on,
            wage_group_id=payload.wage_group_id,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _reload_employee(employee.id))


@router.post("/employees/{employee_id}/status", response=EmployeeOut, auth=django_auth)
def set_employee_status(request, employee_id: UUID, payload: EmployeeStatusIn):
    """Statuswechsel; AUSGETRETEN verlangt ein Austrittsdatum und ist final."""
    actor = _actor_id(request)
    try:
        mitarbeiter_service.set_employee_status(
            actor,
            employee_id=employee_id,
            status=payload.status,
            left_on=payload.left_on,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_employee(employee_id)


@router.post(
    "/employees/{employee_id}/contracts", response={201: ContractOut}, auth=django_auth
)
def create_contract(request, employee_id: UUID, payload: ContractIn):
    """Arbeitsvertrag anlegen; ein laufender Vorgänger wird am Vortag beendet."""
    actor = _actor_id(request)
    hours = {field: getattr(payload, field) for field in _HOUR_FIELDS}
    try:
        contract = mitarbeiter_service.create_contract(
            actor,
            employee_id=employee_id,
            valid_from=payload.valid_from,
            hours=hours,
            vacation_days_per_year=payload.vacation_days_per_year,
            valid_to=payload.valid_to,
            wage_group_id=payload.wage_group_id,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    contract = EmploymentContract.objects.select_related("wage_group").get(id=contract.id)
    return Status(201, _contract_out(contract, date.today()))


@router.post("/contracts/{contract_id}/terminate", response=ContractOut, auth=django_auth)
def terminate_contract(request, contract_id: UUID, payload: TerminateIn):
    """Vertrag kündigen (begründungspflichtig)."""
    actor = _actor_id(request)
    try:
        mitarbeiter_service.terminate_contract(
            actor, contract_id=contract_id, valid_to=payload.valid_to, reason=payload.reason
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    contract = EmploymentContract.objects.select_related("wage_group").filter(
        id=contract_id
    ).first()
    if contract is None:
        raise HttpError(404, "Vertrag nicht gefunden.")
    return _contract_out(contract, date.today())


@router.post(
    "/employees/{employee_id}/absences", response={201: AbsenceOut}, auth=django_auth
)
def create_absence(request, employee_id: UUID, payload: AbsenceIn):
    """Abwesenheitsantrag anlegen (Status ENTWURF). days_count rechnet der Service."""
    actor = _actor_id(request)
    try:
        absence = mitarbeiter_service.create_absence(
            actor,
            employee_id=employee_id,
            absence_type=payload.absence_type,
            start_date=payload.start_date,
            end_date=payload.end_date,
            half_day_start=payload.half_day_start,
            half_day_end=payload.half_day_end,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _absence_out(absence))


def _absence_action(request, absence_id, func, **kwargs):
    actor = _actor_id(request)
    try:
        absence = func(actor, absence_id=absence_id, **kwargs)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _absence_out(absence)


@router.post("/absences/{absence_id}/submit", response=AbsenceOut, auth=django_auth)
def submit_absence(request, absence_id: UUID):
    """Antrag einreichen (ENTWURF → EINGEREICHT)."""
    return _absence_action(request, absence_id, mitarbeiter_service.submit_absence)


@router.post("/absences/{absence_id}/approve", response=AbsenceOut, auth=django_auth)
def approve_absence(request, absence_id: UUID, payload: DecisionIn):
    """Antrag genehmigen (EINGEREICHT → GENEHMIGT)."""
    return _absence_action(
        request, absence_id, mitarbeiter_service.approve_absence, note=payload.note
    )


@router.post("/absences/{absence_id}/reject", response=AbsenceOut, auth=django_auth)
def reject_absence(request, absence_id: UUID, payload: DecisionIn):
    """Antrag ablehnen (begründungspflichtig)."""
    return _absence_action(
        request, absence_id, mitarbeiter_service.reject_absence, note=payload.note
    )


@router.post("/absences/{absence_id}/withdraw", response=AbsenceOut, auth=django_auth)
def withdraw_absence(request, absence_id: UUID):
    """Antrag zurückziehen (aus ENTWURF oder EINGEREICHT)."""
    return _absence_action(request, absence_id, mitarbeiter_service.withdraw_absence)


@router.put(
    "/employees/{employee_id}/vacation-budget",
    response=VacationAccountOut,
    auth=django_auth,
)
def set_vacation_budget(request, employee_id: UUID, payload: VacationBudgetIn):
    """Urlaubskonto eines Jahres setzen (idempotent). Anpassung ist begründungspflichtig."""
    actor = _actor_id(request)
    try:
        mitarbeiter_service.set_vacation_budget(
            actor,
            employee_id=employee_id,
            year=payload.year,
            entitlement_days=payload.entitlement_days,
            carryover_days=payload.carryover_days,
            adjustment_days=payload.adjustment_days,
            adjustment_reason=payload.adjustment_reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return VacationAccountOut(
        **mitarbeiter_service.vacation_account(employee_id, payload.year)
    )
