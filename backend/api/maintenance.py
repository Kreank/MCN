"""Wartungs-API — Wartungsverträge (maintenance.maintenance_contract) inkl.
Fälligkeit und Auslöse-Historie.

Read-only in der Dev-Phase (Anlegen/Statuswechsel/Auslösen laufen über den
wartung-Service, sind aber ohne Auth nicht im UI verdrahtet). Views bleiben dünn.
"""
from datetime import date, datetime
from uuid import UUID

from django.db.models import F, Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from api.permissions import require
from db_core.models import MaintenanceContract, MaintenanceEvent

router = Router()

CONTRACT_STATUSES = ("AKTIV", "INAKTIV", "ARCHIVIERT")


# --- Schemas ---------------------------------------------------------------

class PropertyRefOut(Schema):
    id: UUID
    property_number: str
    name: str
    city: str


class ContractOut(Schema):
    id: UUID
    contract_number: str
    name: str
    status: str
    interval_kind: str
    interval_days: int | None = None
    fixed_date: date | None = None
    due_action: str
    start_date: date
    next_due_date: date | None = None
    lead_time_days: int | None = None
    is_due: bool
    property: PropertyRefOut
    customer: str | None = None
    project_name: str | None = None


class ContractListOut(Schema):
    items: list[ContractOut]
    total: int
    page: int
    page_size: int


class EventOut(Schema):
    occurred_at: datetime
    due_date: date | None = None
    action: str
    result_object_type: str | None = None
    result_object_id: UUID | None = None
    note: str | None = None
    triggered_by: str | None = None


class ContractDetailOut(ContractOut):
    notes: str | None = None
    created_at: datetime
    events: list[EventOut]


class ContractFilter(Schema):
    q: str | None = None
    status: str | None = None
    property_id: UUID | None = None
    due: bool | None = None


# --- Mapper ----------------------------------------------------------------

def _property_ref(contract):
    p = contract.property
    return PropertyRefOut(
        id=p.id, property_number=p.property_number, name=p.name, city=p.address.city
    )


def _contract_out(contract, today):
    is_due = bool(
        contract.status == "AKTIV"
        and contract.next_due_date
        and contract.next_due_date <= today
    )
    return ContractOut(
        id=contract.id,
        contract_number=contract.contract_number,
        name=contract.name,
        status=contract.status,
        interval_kind=contract.interval_kind,
        interval_days=contract.interval_days,
        fixed_date=contract.fixed_date,
        due_action=contract.due_action,
        start_date=contract.start_date,
        next_due_date=contract.next_due_date,
        lead_time_days=contract.lead_time_days,
        is_due=is_due,
        property=_property_ref(contract),
        customer=contract.party.display_name if contract.party_id else None,
        project_name=contract.project.name if contract.project_id else None,
    )


# --- Lesende Endpoints (Dev-Phase ohne Auth) -------------------------------

@router.get("/contracts", response=ContractListOut)
def list_contracts(
    request,
    filters: ContractFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Wartungsverträge auflisten: Suche (Name/Nummer), Status-/Objekt-/
    Fälligkeitsfilter. Sortiert nach nächster Fälligkeit (fällige zuerst)."""
    require(request, "workflow", "LESEN")
    if filters.status and filters.status not in CONTRACT_STATUSES:
        raise HttpError(422, f"Unbekannter Status '{filters.status}'.")

    today = date.today()
    qs = MaintenanceContract.objects.select_related(
        "property__address", "party", "project"
    )
    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(
            Q(name__icontains=needle) | Q(contract_number__icontains=needle)
        )
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.property_id:
        qs = qs.filter(property_id=filters.property_id)
    if filters.due:
        qs = qs.filter(status="AKTIV", next_due_date__lte=today)
    qs = qs.order_by(F("next_due_date").asc(nulls_last=True), "-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_contract_out(c, today) for c in qs[start:start + page_size]]
    return ContractListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/contracts/{contract_id}", response=ContractDetailOut)
def get_contract(request, contract_id: UUID):
    """Detail eines Wartungsvertrags inkl. Auslöse-Historie."""
    require(request, "workflow", "LESEN")
    today = date.today()
    contract = (
        MaintenanceContract.objects.filter(id=contract_id)
        .select_related("property__address", "party", "project")
        .first()
    )
    if contract is None:
        raise HttpError(404, "Wartungsvertrag nicht gefunden.")

    events = [
        EventOut(
            occurred_at=e.occurred_at,
            due_date=e.due_date,
            action=e.action,
            result_object_type=e.result_object_type,
            result_object_id=e.result_object_id,
            note=e.note,
            triggered_by=e.triggered_by.display_name if e.triggered_by_id else None,
        )
        for e in MaintenanceEvent.objects.filter(contract_id=contract.id)
        .select_related("triggered_by")
        .order_by("-occurred_at")
    ]

    base = _contract_out(contract, today)
    return ContractDetailOut(
        **base.model_dump(),
        notes=contract.notes,
        created_at=contract.created_at,
        events=events,
    )
