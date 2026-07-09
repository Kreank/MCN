"""Buchhaltungs-API — offene Posten, Zahlungen und Mahnwesen zu veröffentlichten
Rechnungen.

Read-only in der Dev-Phase (Zahlung/Storno/Mahnung laufen über den
buchhaltung-Service, sind aber ohne Auth nicht im UI verdrahtet). Der
Zahlungsstatus und der offene Betrag sind in der DB NICHT gespeichert — sie
werden aus der vorzeichenbehafteten Summe der Zahlungen abgeleitet
(PAYMENT_SIGN, dieselbe Konvention wie im Service).
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import (
    Case,
    DecimalField,
    F,
    Max,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.models import DunningLevel, DunningNotice, Invoice, Payment
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services.buchhaltung import PAYMENT_SIGN

router = Router()

_POS = tuple(t for t, s in PAYMENT_SIGN.items() if s > 0)
_NEG = tuple(t for t, s in PAYMENT_SIGN.items() if s < 0)
_ZERO = Decimal("0.00")

PAYMENT_STATUSES = ("OFFEN", "TEILZAHLUNG", "BEZAHLT", "UEBERZAHLT")


# --- Ableitungen -----------------------------------------------------------

def _paid_subquery():
    """Subquery: vorzeichenbehaftete Summe der Zahlungen je Rechnung (als eigene
    Aggregation, damit kein Join-Kreuzprodukt mit anderen Relationen entsteht)."""
    return Subquery(
        Payment.objects.filter(invoice_id=OuterRef("pk"))
        .values("invoice_id")
        .annotate(
            s=Sum(
                Case(
                    When(payment_type__in=_POS, then=F("amount")),
                    When(payment_type__in=_NEG, then=-F("amount")),
                    default=Value(0),
                    output_field=DecimalField(max_digits=15, decimal_places=2),
                )
            )
        )
        .values("s"),
        output_field=DecimalField(max_digits=15, decimal_places=2),
    )


def _level_subquery():
    return Subquery(
        DunningNotice.objects.filter(invoice_id=OuterRef("pk"))
        .values("invoice_id")
        .annotate(m=Max("level"))
        .values("m")
    )


def _payment_status(paid: Decimal, gross: Decimal) -> str:
    if paid <= _ZERO:
        return "OFFEN"
    if paid < gross:
        return "TEILZAHLUNG"
    if paid == gross:
        return "BEZAHLT"
    return "UEBERZAHLT"


def _debtor_name(invoice):
    """Primärer Rechnungsschuldner (INVOICE_DEBTOR) als Anzeige, sonst None."""
    debtor = None
    for p in invoice.parties.all():
        if p.role == "INVOICE_DEBTOR":
            debtor = p
            if p.is_primary:
                break
    return debtor.party.display_name if debtor else None


# --- Schemas ---------------------------------------------------------------

class OpenItemOut(Schema):
    id: UUID
    invoice_number: str | None = None
    invoice_type: str
    status: str
    debtor: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    gross_total: Decimal | None = None
    paid_total: Decimal
    open_amount: Decimal
    payment_status: str
    is_overdue: bool
    dunning_level: int | None = None


class OpenItemListOut(Schema):
    items: list[OpenItemOut]
    total: int
    page: int
    page_size: int


class PaymentOut(Schema):
    id: UUID
    payment_type: str
    amount: Decimal
    currency: str
    paid_at: date
    import_source: str
    # Storno-Kennzeichnung für das UI (was ist noch stornierbar?):
    is_reversal: bool  # selbst eine Stornobuchung (payment_type STORNO_BUCHUNG)
    is_reversed: bool  # bereits durch eine Gegenbuchung storniert
    is_reversible: bool  # eingehende Zahlung, noch nicht storniert → reverse möglich


class PaymentDetailOut(PaymentOut):
    invoice_id: UUID
    external_reference: str


class PaymentRecordIn(Schema):
    amount: Decimal
    paid_at: date
    payment_type: str = "ZAHLUNG"
    external_reference: str | None = None
    currency: str = "EUR"


class PaymentReverseIn(Schema):
    paid_at: date | None = None


class DunningIssueIn(Schema):
    level: int
    issued_at: date
    note: str | None = None


class DunningNoticeOut(Schema):
    level: int
    label: str
    issued_at: date
    note: str | None = None
    created_by: str | None = None


class InvoiceRefOut(Schema):
    id: UUID
    property_name: str
    project_name: str | None = None
    work_order_number: str | None = None


class CreditRefOut(Schema):
    id: UUID
    invoice_number: str | None = None
    invoice_type: str
    gross_total: Decimal | None = None


class OpenItemDetailOut(OpenItemOut):
    currency: str
    net_total: Decimal | None = None
    tax_total: Decimal | None = None
    reference: InvoiceRefOut
    # Bei Gutschrift/Storno: der referenzierte Ursprungsbeleg.
    origin: CreditRefOut | None = None
    # Storno-/Gutschriftbelege, die auf DIESE Rechnung verweisen.
    credit_notes: list[CreditRefOut]
    payments: list[PaymentOut]
    dunning: list[DunningNoticeOut]


class CorrectionIn(Schema):
    positions: list[int]


class DunningRowOut(Schema):
    id: UUID
    invoice_number: str | None = None
    debtor: str | None = None
    due_date: date | None = None
    gross_total: Decimal | None = None
    open_amount: Decimal
    dunning_level: int | None = None
    last_issued_at: date | None = None
    days_overdue: int | None = None


class DunningListOut(Schema):
    items: list[DunningRowOut]
    levels: list[dict]


class OpenItemFilter(Schema):
    q: str | None = None
    payment_status: str | None = None
    overdue: bool | None = None
    invoice_type: str | None = None


# --- Mapper ----------------------------------------------------------------

# Storno-Buchungen tragen die Referenz 'STORNO:<id-der-Ursprungszahlung>'
# (buchhaltung-Service, es gibt keinen FK). Daraus leitet das UI ab, welche
# Zahlung schon storniert ist.
_STORNO_PREFIX = "STORNO:"


def _reversed_ids(payments):
    """IDs (als str) der Zahlungen, zu denen bereits eine Stornobuchung existiert."""
    out = set()
    for p in payments:
        ref = p.external_reference or ""
        if ref.startswith(_STORNO_PREFIX):
            out.add(ref[len(_STORNO_PREFIX):])
    return out


def _payment_flags(p, reversed_ids):
    """(is_reversal, is_reversed, is_reversible) — Stornierbarkeit fürs UI.

    is_reversible spiegelt exakt die Service-Regel (reverse_payment): nur eine
    eingehende (positiv gewertete) Zahlung, die nicht bereits storniert wurde,
    lässt sich stornieren. Rückerstattungen und Storno-Buchungen (negativ) nicht.
    """
    is_reversal = p.payment_type == "STORNO_BUCHUNG"
    is_reversed = str(p.id) in reversed_ids
    is_reversible = PAYMENT_SIGN.get(p.payment_type, 0) > 0 and not is_reversed
    return is_reversal, is_reversed, is_reversible


def _payment_out(p, reversed_ids):
    is_reversal, is_reversed, is_reversible = _payment_flags(p, reversed_ids)
    return PaymentOut(
        id=p.id,
        payment_type=p.payment_type,
        amount=p.amount,
        currency=p.currency,
        paid_at=p.paid_at,
        import_source=p.import_source,
        is_reversal=is_reversal,
        is_reversed=is_reversed,
        is_reversible=is_reversible,
    )


def _payment_detail(p):
    # Einzelzahlung (Schreib-Antwort): den Storno-Status je Rechnung frisch laden.
    reversed_ids = _reversed_ids(
        Payment.objects.filter(
            invoice_id=p.invoice_id, external_reference__startswith=_STORNO_PREFIX
        )
    )
    is_reversal, is_reversed, is_reversible = _payment_flags(p, reversed_ids)
    return PaymentDetailOut(
        id=p.id,
        invoice_id=p.invoice_id,
        payment_type=p.payment_type,
        amount=p.amount,
        currency=p.currency,
        paid_at=p.paid_at,
        import_source=p.import_source,
        external_reference=p.external_reference,
        is_reversal=is_reversal,
        is_reversed=is_reversed,
        is_reversible=is_reversible,
    )


def _open_item_out(inv, today):
    gross = inv.gross_total or _ZERO
    paid = inv.paid_total if inv.paid_total is not None else _ZERO
    open_amount = gross - paid
    status = _payment_status(paid, gross)
    is_overdue = bool(
        inv.due_date and inv.due_date < today and open_amount > _ZERO
    )
    return OpenItemOut(
        id=inv.id,
        invoice_number=inv.invoice_number,
        invoice_type=inv.invoice_type,
        status=inv.status,
        debtor=_debtor_name(inv),
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        gross_total=inv.gross_total,
        paid_total=paid,
        open_amount=open_amount,
        payment_status=status,
        is_overdue=is_overdue,
        dunning_level=inv.dunning_level,
    )


# --- Lesende Endpoints (Dev-Phase ohne Auth) -------------------------------

@router.get("/invoices", response=OpenItemListOut)
def list_open_items(
    request,
    filters: OpenItemFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Offene Posten: veröffentlichte Rechnungen mit abgeleitetem Zahlungsstatus
    und offenem Betrag. Filter: Zahlungsstatus, überfällig, Belegart, Suche
    (Rechnungsnummer)."""
    require(request, "invoicing", "LESEN")
    if filters.payment_status and filters.payment_status not in PAYMENT_STATUSES:
        raise HttpError(422, f"Unbekannter payment_status '{filters.payment_status}'.")

    today = date.today()
    qs = (
        Invoice.objects.filter(status="VEROEFFENTLICHT")
        .annotate(
            paid_total=Coalesce(
                _paid_subquery(),
                Value(_ZERO, output_field=DecimalField(max_digits=15, decimal_places=2)),
            ),
            dunning_level=_level_subquery(),
        )
        .prefetch_related("parties__party")
    )
    if filters.q:
        qs = qs.filter(invoice_number__icontains=filters.q.strip())
    if filters.invoice_type:
        qs = qs.filter(invoice_type=filters.invoice_type)
    if filters.overdue:
        qs = qs.filter(
            due_date__lt=today, gross_total__gt=F("paid_total")
        )
    qs = qs.order_by(F("due_date").asc(nulls_last=True), "-created_at", "id")

    start = (page - 1) * page_size
    if filters.payment_status:
        # Der Zahlungsstatus ist abgeleitet (nicht in SQL filterbar) → alle mappen,
        # in Python filtern und erst dann paginieren. Nur dieser Zweig materialisiert
        # die volle Ergebnismenge.
        items = [
            it
            for it in (_open_item_out(inv, today) for inv in qs)
            if it.payment_status == filters.payment_status
        ]
        total = len(items)
        window = items[start:start + page_size]
    else:
        total = qs.count()
        window = [_open_item_out(inv, today) for inv in qs[start:start + page_size]]
    return OpenItemListOut(
        items=window, total=total, page=page, page_size=page_size
    )


@router.get("/invoices/{invoice_id}", response=OpenItemDetailOut)
def get_open_item(request, invoice_id: UUID):
    """Detail eines offenen Postens inkl. Zahlungen und Mahnverlauf."""
    require(request, "invoicing", "LESEN")
    today = date.today()
    inv = (
        Invoice.objects.filter(id=invoice_id, status="VEROEFFENTLICHT")
        .annotate(
            paid_total=Coalesce(
                _paid_subquery(),
                Value(_ZERO, output_field=DecimalField(max_digits=15, decimal_places=2)),
            ),
            dunning_level=_level_subquery(),
        )
        .select_related("property__address", "project", "work_order")
        .prefetch_related("parties__party")
        .first()
    )
    if inv is None:
        raise HttpError(404, "Veröffentlichte Rechnung nicht gefunden.")

    payment_rows = list(
        Payment.objects.filter(invoice_id=inv.id).order_by("paid_at", "imported_at")
    )
    reversed_ids = _reversed_ids(payment_rows)
    payments = [_payment_out(p, reversed_ids) for p in payment_rows]
    notices = [
        DunningNoticeOut(
            level=n.level_id,
            label=n.level.label,
            issued_at=n.issued_at,
            note=n.note,
            created_by=n.created_by.display_name if n.created_by_id else None,
        )
        for n in DunningNotice.objects.filter(invoice_id=inv.id)
        .select_related("level", "created_by")
        .order_by("level")
    ]

    base = _open_item_out(inv, today)
    reference = InvoiceRefOut(
        id=inv.id,
        property_name=f"{inv.property.name} · {inv.property.address.city}",
        project_name=inv.project.name if inv.project_id else None,
        work_order_number=inv.work_order.order_number if inv.work_order_id else None,
    )
    origin = None
    if inv.reference_invoice_id:
        o = Invoice.objects.filter(id=inv.reference_invoice_id).first()
        if o is not None:
            origin = CreditRefOut(
                id=o.id, invoice_number=o.invoice_number,
                invoice_type=o.invoice_type, gross_total=o.gross_total,
            )
    credit_notes = [
        CreditRefOut(
            id=c.id, invoice_number=c.invoice_number,
            invoice_type=c.invoice_type, gross_total=c.gross_total,
        )
        for c in Invoice.objects.filter(
            reference_invoice_id=inv.id, status="VEROEFFENTLICHT"
        ).order_by("created_at")
    ]
    return OpenItemDetailOut(
        **base.model_dump(),
        currency=inv.currency,
        net_total=inv.net_total,
        tax_total=inv.tax_total,
        reference=reference,
        origin=origin,
        credit_notes=credit_notes,
        payments=payments,
        dunning=notices,
    )


# --- Schreibende Endpoints (Session-Auth Pflicht) --------------------------

@router.post("/invoices/{invoice_id}/cancel", response={201: CreditRefOut}, auth=django_auth)
def cancel_invoice(request, invoice_id: UUID):
    """Storniert eine veröffentlichte Rechnung durch einen Stornobeleg (STORNO)."""
    actor, _ = require(request, "invoicing", "STORNIEREN")
    try:
        credit = beleg_service.create_cancellation(actor, invoice_id=invoice_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(
        201,
        CreditRefOut(
            id=credit.id, invoice_number=credit.invoice_number,
            invoice_type=credit.invoice_type, gross_total=credit.gross_total,
        ),
    )


@router.post(
    "/invoices/{invoice_id}/correction", response={201: CreditRefOut}, auth=django_auth
)
def correct_invoice(request, invoice_id: UUID, payload: CorrectionIn):
    """Erzeugt eine Rechnungskorrektur (GUTSCHRIFT) über die angegebenen Positionen."""
    # Korrektur/Gutschrift eines veröffentlichten Belegs = Storno-Folgebeleg → STORNIEREN.
    actor, _ = require(request, "invoicing", "STORNIEREN")
    try:
        credit = beleg_service.create_correction(
            actor, invoice_id=invoice_id, positions=payload.positions
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(
        201,
        CreditRefOut(
            id=credit.id, invoice_number=credit.invoice_number,
            invoice_type=credit.invoice_type, gross_total=credit.gross_total,
        ),
    )


@router.get("/dunning", response=DunningListOut)
def list_dunning(request, level: int | None = Query(None)):
    """Mahnliste: veröffentlichte Rechnungen, die überfällig mit offenem Betrag
    sind oder bereits gemahnt wurden. Optional nach aktueller Mahnstufe gefiltert
    (level=0 → überfällig, aber noch ungemahnt)."""
    require(request, "invoicing", "LESEN")
    today = date.today()
    qs = (
        Invoice.objects.filter(status="VEROEFFENTLICHT")
        .annotate(
            paid_total=Coalesce(
                _paid_subquery(),
                Value(_ZERO, output_field=DecimalField(max_digits=15, decimal_places=2)),
            ),
            dunning_level=_level_subquery(),
        )
        .filter(
            Q(due_date__lt=today, gross_total__gt=F("paid_total"))
            | Q(dunning_level__isnull=False)
        )
        .prefetch_related("parties__party")
        .order_by(F("due_date").asc(nulls_last=True), "id")
    )

    last_issued = {
        row["invoice_id"]: row["m"]
        for row in DunningNotice.objects.filter(invoice_id__in=[i.id for i in qs])
        .values("invoice_id")
        .annotate(m=Max("issued_at"))
    }

    items = []
    for inv in qs:
        cur = inv.dunning_level or 0
        if level is not None and cur != level:
            continue
        gross = inv.gross_total or _ZERO
        paid = inv.paid_total or _ZERO
        days_overdue = (today - inv.due_date).days if inv.due_date else None
        items.append(
            DunningRowOut(
                id=inv.id,
                invoice_number=inv.invoice_number,
                debtor=_debtor_name(inv),
                due_date=inv.due_date,
                gross_total=inv.gross_total,
                open_amount=gross - paid,
                dunning_level=inv.dunning_level,
                last_issued_at=last_issued.get(inv.id),
                days_overdue=days_overdue,
            )
        )

    levels = [
        {"level": lv.level, "label": lv.label, "days_after_due": lv.days_after_due}
        for lv in DunningLevel.objects.order_by("level")
    ]
    return DunningListOut(items=items, levels=levels)


# --- Schreibende Endpoints: Zahlungen und Mahnwesen ------------------------

@router.post(
    "/invoices/{invoice_id}/payments", response={201: PaymentDetailOut}, auth=django_auth
)
def record_payment(request, invoice_id: UUID, payload: PaymentRecordIn):
    """Erfasst eine (Teil-)Zahlung zu einer veröffentlichten Rechnung.

    Recht AENDERN: eine Zahlung verändert den offenen Posten einer bestehenden
    Rechnung — kein Freigabe-/Storno-Vorgang, sondern die laufende Pflege des
    Zahlungsstands. amount ist stets positiv; das Vorzeichen ergibt sich aus dem
    payment_type (PAYMENT_SIGN). Die DB-Tore (nur veröffentlichte Rechnung B-23,
    Idempotenz) landen als 422 beim Aufrufer.
    """
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        payment = buchhaltung_service.record_payment(
            actor,
            invoice_id=invoice_id,
            amount=payload.amount,
            paid_at=payload.paid_at,
            payment_type=payload.payment_type,
            external_reference=payload.external_reference,
            currency=payload.currency,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _payment_detail(payment))


@router.post("/payments/{payment_id}/reverse", response=PaymentDetailOut, auth=django_auth)
def reverse_payment(request, payment_id: UUID, payload: PaymentReverseIn):
    """Storniert eine Zahlung durch eine Gegenbuchung (STORNO_BUCHUNG).

    Recht STORNIEREN: eine erfasste Zahlung nachträglich unwirksam zu machen ist
    ein Storno-Vorgang, nicht die normale Pflege — deshalb das eigene, engere
    Recht. Physisch wird nichts gelöscht (append-only); nur eingehende Zahlungen
    lassen sich stornieren, und keine doppelt (→ 422).
    """
    actor, _ = require(request, "invoicing", "STORNIEREN")
    try:
        storno = buchhaltung_service.reverse_payment(
            actor, payment_id=payment_id, paid_at=payload.paid_at
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _payment_detail(storno)


@router.post(
    "/invoices/{invoice_id}/dunning", response={201: DunningNoticeOut}, auth=django_auth
)
def issue_dunning_notice(request, invoice_id: UUID, payload: DunningIssueIn):
    """Erzeugt eine Mahnstufe (Zahlungserinnerung/Mahnung) zu einer Rechnung.

    Recht VERSENDEN: eine Mahnung ist eine nach außen wirkende Kundenkommunikation
    — dasselbe Recht, das auch Angebots-/Belegversand trägt. Die DB erzwingt eine
    veröffentlichte, zum issued_at fällige Rechnung und die nächste lückenlose
    Stufe (max+1); Verstöße kommen als 422 zurück.
    """
    actor, _ = require(request, "invoicing", "VERSENDEN")
    try:
        notice = buchhaltung_service.issue_dunning_notice(
            actor,
            invoice_id=invoice_id,
            level=payload.level,
            issued_at=payload.issued_at,
            note=payload.note,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    notice = (
        DunningNotice.objects.select_related("level", "created_by")
        .get(id=notice.id)
    )
    return Status(
        201,
        DunningNoticeOut(
            level=notice.level_id,
            label=notice.level.label,
            issued_at=notice.issued_at,
            note=notice.note,
            created_by=notice.created_by.display_name if notice.created_by_id else None,
        ),
    )
