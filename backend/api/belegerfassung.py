"""Belegerfassungs-API — Eingangsbelege, Buchungskonten und Kostenstellen.

Fachschema `accounting` (Migrationen 0030–0032), Modul-Recht `accounting`.
Jeder Endpunkt prüft ein Recht (fail-closed über `require`); Schreibpfade laufen
über den belegerfassung-Service (business_transaction, DB-Tore → 422). Beträge
werden serverseitig aus den Positionen berechnet — der Client liefert KEINE
Summen (wie der Belegeditor). Die Freigabe (Status FREIGEGEBEN) und die Buchung
(GEBUCHT) sind Tore mit dem eigenen Recht FREIGEBEN.
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Q
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.models import (
    CostCenter,
    LedgerAccount,
    Receipt,
    ReceiptLine,
    StatusChange,
)
from db_core.services import belegerfassung as service

router = Router()


# --- Schemas: Buchungskonten -----------------------------------------------

class LedgerAccountOut(Schema):
    id: UUID
    account_number: str
    label: str
    account_type: str
    chart_of_accounts: str | None = None
    active: bool
    notes: str | None = None


class LedgerAccountIn(Schema):
    account_number: str
    label: str
    account_type: str
    chart_of_accounts: str | None = None
    notes: str | None = None


class LedgerAccountPatch(Schema):
    account_number: str | None = None
    label: str | None = None
    account_type: str | None = None
    chart_of_accounts: str | None = None
    active: bool | None = None
    notes: str | None = None


# --- Schemas: Kostenstellen ------------------------------------------------

class CostCenterOut(Schema):
    id: UUID
    code: str
    label: str
    active: bool
    notes: str | None = None


class CostCenterIn(Schema):
    code: str
    label: str
    notes: str | None = None


class CostCenterPatch(Schema):
    code: str | None = None
    label: str | None = None
    active: bool | None = None
    notes: str | None = None


# --- Schemas: Eingangsbeleg ------------------------------------------------

class ReceiptLineIn(Schema):
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_code: str
    unit: str | None = None
    ledger_account_id: UUID | None = None
    cost_center_id: UUID | None = None


class ReceiptLineOut(Schema):
    id: UUID
    position_number: int
    description: str
    quantity: Decimal
    unit: str | None = None
    unit_price: Decimal
    tax_code: str
    tax_rate_percent: Decimal
    net_amount: Decimal
    ledger_account_id: UUID | None = None
    ledger_account_label: str | None = None
    cost_center_id: UUID | None = None
    cost_center_label: str | None = None


class ReceiptRowOut(Schema):
    id: UUID
    receipt_number: str
    supplier: str | None = None
    supplier_invoice_number: str | None = None
    receipt_date: date
    due_date: date | None = None
    currency: str
    net_total: Decimal
    tax_total: Decimal
    gross_total: Decimal
    status: str


class ReceiptListOut(Schema):
    items: list[ReceiptRowOut]
    total: int
    page: int
    page_size: int


class StatusEventOut(Schema):
    from_status: str | None = None
    to_status: str
    reason: str | None = None
    changed_by: str | None = None
    occurred_at: datetime


class ReceiptDetailOut(ReceiptRowOut):
    supplier_party_id: UUID
    received_date: date
    rejection_reason: str | None = None
    notes: str | None = None
    lines: list[ReceiptLineOut]
    history: list[StatusEventOut]


class ReceiptCreateIn(Schema):
    supplier_party_id: UUID
    receipt_date: date
    lines: list[ReceiptLineIn]
    received_date: date | None = None
    due_date: date | None = None
    supplier_invoice_number: str | None = None
    currency: str = "EUR"
    notes: str | None = None


class ReceiptUpdateIn(Schema):
    supplier_party_id: UUID | None = None
    receipt_date: date | None = None
    received_date: date | None = None
    currency: str | None = None
    lines: list[ReceiptLineIn] | None = None
    # Optionale Leer-Felder (None = leeren); weglassen = unverändert.
    due_date: date | None = None
    supplier_invoice_number: str | None = None
    notes: str | None = None


class StatusIn(Schema):
    to_status: str
    reason: str | None = None


# --- Mapper ----------------------------------------------------------------

def _ledger_out(a):
    return LedgerAccountOut(
        id=a.id, account_number=a.account_number, label=a.label,
        account_type=a.account_type, chart_of_accounts=a.chart_of_accounts,
        active=a.active, notes=a.notes,
    )


def _cost_center_out(c):
    return CostCenterOut(
        id=c.id, code=c.code, label=c.label, active=c.active, notes=c.notes
    )


def _line_out(line):
    return ReceiptLineOut(
        id=line.id,
        position_number=line.position_number,
        description=line.description,
        quantity=line.quantity,
        unit=line.unit,
        unit_price=line.unit_price,
        tax_code=line.tax_code_id,
        tax_rate_percent=line.tax_rate_percent,
        net_amount=line.net_amount,
        ledger_account_id=line.ledger_account_id,
        ledger_account_label=(
            f"{line.ledger_account.account_number} — {line.ledger_account.label}"
            if line.ledger_account_id else None
        ),
        cost_center_id=line.cost_center_id,
        cost_center_label=(
            f"{line.cost_center.code} — {line.cost_center.label}"
            if line.cost_center_id else None
        ),
    )


def _row_out(r):
    return ReceiptRowOut(
        id=r.id,
        receipt_number=r.receipt_number,
        supplier=r.supplier_party.display_name if r.supplier_party_id else None,
        supplier_invoice_number=r.supplier_invoice_number,
        receipt_date=r.receipt_date,
        due_date=r.due_date,
        currency=r.currency,
        net_total=r.net_total,
        tax_total=r.tax_total,
        gross_total=r.gross_total,
        status=r.status,
    )


# --- Buchungskonten --------------------------------------------------------

@router.get("/ledger-accounts", response=list[LedgerAccountOut])
def list_ledger_accounts(request, include_inactive: bool = True):
    require(request, "accounting", "LESEN")
    return [
        _ledger_out(a)
        for a in service.list_ledger_accounts(include_inactive=include_inactive)
    ]


@router.post("/ledger-accounts", response={201: LedgerAccountOut}, auth=django_auth)
def create_ledger_account(request, payload: LedgerAccountIn):
    actor, _ = require(request, "accounting", "ANLEGEN")
    try:
        account = service.create_ledger_account(actor, **payload.model_dump())
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _ledger_out(account))


@router.put("/ledger-accounts/{account_id}", response=LedgerAccountOut, auth=django_auth)
def update_ledger_account(request, account_id: UUID, payload: LedgerAccountPatch):
    actor, _ = require(request, "accounting", "AENDERN")
    try:
        account = service.update_ledger_account(
            actor, ledger_account_id=account_id, **payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _ledger_out(account)


# --- Kostenstellen ---------------------------------------------------------

@router.get("/cost-centers", response=list[CostCenterOut])
def list_cost_centers(request, include_inactive: bool = True):
    require(request, "accounting", "LESEN")
    return [
        _cost_center_out(c)
        for c in service.list_cost_centers(include_inactive=include_inactive)
    ]


@router.post("/cost-centers", response={201: CostCenterOut}, auth=django_auth)
def create_cost_center(request, payload: CostCenterIn):
    actor, _ = require(request, "accounting", "ANLEGEN")
    try:
        cc = service.create_cost_center(actor, **payload.model_dump())
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _cost_center_out(cc))


@router.put("/cost-centers/{cost_center_id}", response=CostCenterOut, auth=django_auth)
def update_cost_center(request, cost_center_id: UUID, payload: CostCenterPatch):
    actor, _ = require(request, "accounting", "AENDERN")
    try:
        cc = service.update_cost_center(
            actor, cost_center_id=cost_center_id, **payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _cost_center_out(cc)


# --- Eingangsbelege --------------------------------------------------------

@router.get("/receipts", response=ReceiptListOut)
def list_receipts(
    request,
    q: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
):
    require(request, "accounting", "LESEN")
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    qs = Receipt.objects.select_related("supplier_party").all()
    if status:
        if status not in service.RECEIPT_STATUSES:
            raise HttpError(422, f"Unbekannter Status '{status}'.")
        qs = qs.filter(status=status)
    if q:
        term = q.strip()
        qs = qs.filter(
            Q(receipt_number__icontains=term)
            | Q(supplier_invoice_number__icontains=term)
        )
    qs = qs.order_by("-receipt_date", "-created_at", "id")
    total = qs.count()
    start = (page - 1) * page_size
    window = [_row_out(r) for r in qs[start:start + page_size]]
    return ReceiptListOut(items=window, total=total, page=page, page_size=page_size)


@router.get("/receipts/{receipt_id}", response=ReceiptDetailOut)
def get_receipt(request, receipt_id: UUID):
    require(request, "accounting", "LESEN")
    r = (
        Receipt.objects.select_related("supplier_party")
        .filter(id=receipt_id)
        .first()
    )
    if r is None:
        raise HttpError(404, "Eingangsbeleg nicht gefunden.")
    lines = [
        _line_out(line)
        for line in ReceiptLine.objects.filter(receipt_id=r.id)
        .select_related("ledger_account", "cost_center")
        .order_by("position_number")
    ]
    history = [
        StatusEventOut(
            from_status=ev.from_status,
            to_status=ev.to_status,
            reason=ev.reason,
            changed_by=ev.changed_by.display_name if ev.changed_by_id else None,
            occurred_at=ev.occurred_at,
        )
        for ev in StatusChange.objects.filter(entity="receipt", entity_id=r.id)
        .select_related("changed_by")
        .order_by("occurred_at")
    ]
    return ReceiptDetailOut(
        **_row_out(r).model_dump(),
        supplier_party_id=r.supplier_party_id,
        received_date=r.received_date,
        rejection_reason=r.rejection_reason,
        notes=r.notes,
        lines=lines,
        history=history,
    )


@router.post("/receipts", response={201: ReceiptDetailOut}, auth=django_auth)
def create_receipt(request, payload: ReceiptCreateIn):
    actor, _ = require(request, "accounting", "ANLEGEN")
    data = payload.model_dump()
    try:
        receipt = service.create_receipt(actor, **data)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, get_receipt(request, receipt.id))


@router.put("/receipts/{receipt_id}", response=ReceiptDetailOut, auth=django_auth)
def update_receipt(request, receipt_id: UUID, payload: ReceiptUpdateIn):
    actor, _ = require(request, "accounting", "AENDERN")
    fields = payload.model_dump(exclude_unset=True)
    try:
        service.update_receipt(actor, receipt_id=receipt_id, **fields)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return get_receipt(request, receipt_id)


@router.post("/receipts/{receipt_id}/status", response=ReceiptDetailOut, auth=django_auth)
def advance_receipt_status(request, receipt_id: UUID, payload: StatusIn):
    """Statuswechsel eines Eingangsbelegs.

    FREIGEGEBEN/GEBUCHT sind Tore → Recht FREIGEBEN (nur BUCHHALTUNG/GF/ADMIN);
    die übrigen Wechsel (prüfen, zurücksetzen, ablehnen) laufen über AENDERN.
    """
    action = "FREIGEBEN" if payload.to_status in ("FREIGEGEBEN", "GEBUCHT") else "AENDERN"
    actor, _ = require(request, "accounting", action)
    try:
        service.advance_status(
            actor, receipt_id=receipt_id, to_status=payload.to_status,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return get_receipt(request, receipt_id)
