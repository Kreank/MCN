"""Beleg-API — Angebote (invoicing.quote) inkl. Positionen.

Lesen in der Dev-Phase ohne Auth; Anlegen verlangt Django-Session + app_user.
Deckt Anlage bis ENTWURF sowie Liste/Detail ab (Versand-Workflow folgt separat).
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Q
from django.http import HttpResponse
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.models import Invoice, Quote
from db_core.services import beleg as beleg_service
from db_core.services import beleg_pdf as beleg_pdf_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class PropertyRefOut(Schema):
    id: UUID
    property_number: str
    name: str
    city: str


class QuoteOut(Schema):
    id: UUID
    quote_number: str | None = None
    title: str
    status: str
    currency: str
    quote_date: date | None = None
    net_total: Decimal | None = None
    gross_total: Decimal | None = None
    property: PropertyRefOut


class QuoteListOut(Schema):
    items: list[QuoteOut]
    total: int
    page: int
    page_size: int


class QuoteLineOut(Schema):
    position_number: int
    line_type: str
    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    discount_percent: Decimal | None = None
    tax_code: str | None = None
    tax_rate_percent: Decimal | None = None
    net_amount: Decimal | None = None


class ProjectRefOut(Schema):
    id: UUID
    project_number: str
    name: str


class QuoteDetailOut(QuoteOut):
    valid_until_date: date | None = None
    tax_total: Decimal | None = None
    version: int
    project: ProjectRefOut | None = None
    sent_at: datetime | None = None
    has_snapshot: bool = False
    content_hash: str | None = None
    lines: list[QuoteLineOut]


class QuoteLineIn(Schema):
    line_type: str
    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    discount_percent: Decimal | None = None
    tax_code: str | None = None


class QuoteIn(Schema):
    property_id: UUID
    title: str
    project_id: UUID | None = None
    quote_date: date | None = None
    valid_until_date: date | None = None
    lines: list[QuoteLineIn] = []


class QuoteFilter(Schema):
    q: str | None = None
    status: str | None = None
    property_id: UUID | None = None
    project_id: UUID | None = None


def _property_ref(quote):
    p = quote.property
    return PropertyRefOut(
        id=p.id, property_number=p.property_number, name=p.name, city=p.address.city
    )


def _quote_out(quote):
    return QuoteOut(
        id=quote.id,
        quote_number=quote.quote_number,
        title=quote.title,
        status=quote.status,
        currency=quote.currency,
        quote_date=quote.quote_date,
        net_total=quote.net_total,
        gross_total=quote.gross_total,
        property=_property_ref(quote),
    )


# --- Lesende Endpoints -----------------------------------------------------

@router.get("/quotes", response=QuoteListOut)
def list_quotes(
    request,
    filters: QuoteFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Angebote auflisten: Suche (Titel/Nummer), Status-/Liegenschafts-/Projektfilter."""
    require(request, "invoicing", "LESEN")
    qs = Quote.objects.select_related("property__address")

    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(Q(title__icontains=needle) | Q(quote_number__icontains=needle))
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.property_id:
        qs = qs.filter(property_id=filters.property_id)
    if filters.project_id:
        qs = qs.filter(project_id=filters.project_id)

    qs = qs.order_by("-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_quote_out(q) for q in qs[start:start + page_size]]
    return QuoteListOut(items=items, total=total, page=page, page_size=page_size)


def _quote_detail(quote_id):
    quote = (
        Quote.objects.filter(id=quote_id)
        .select_related("property__address", "project")
        .prefetch_related("lines")
        .first()
    )
    if quote is None:
        raise HttpError(404, "Angebot nicht gefunden.")

    lines = [
        QuoteLineOut(
            position_number=l.position_number,
            line_type=l.line_type,
            description=l.description,
            quantity=l.quantity,
            unit=l.unit,
            unit_price=l.unit_price,
            discount_percent=l.discount_percent,
            tax_code=l.tax_code_id,
            tax_rate_percent=l.tax_rate_percent,
            net_amount=l.net_amount,
        )
        for l in sorted(quote.lines.all(), key=lambda l: l.position_number)
    ]
    project = (
        ProjectRefOut(
            id=quote.project.id,
            project_number=quote.project.project_number,
            name=quote.project.name,
        )
        if quote.project_id
        else None
    )
    return QuoteDetailOut(
        id=quote.id,
        quote_number=quote.quote_number,
        title=quote.title,
        status=quote.status,
        currency=quote.currency,
        quote_date=quote.quote_date,
        valid_until_date=quote.valid_until_date,
        net_total=quote.net_total,
        tax_total=quote.tax_total,
        gross_total=quote.gross_total,
        version=quote.version,
        property=_property_ref(quote),
        project=project,
        sent_at=quote.sent_at,
        has_snapshot=quote.billing_snapshot is not None,
        content_hash=quote.content_hash,
        lines=lines,
    )


# --- Schreibender Endpoint (Session-Auth Pflicht) --------------------------

@router.post("/quotes", response={201: QuoteDetailOut}, auth=django_auth)
def create_quote(request, payload: QuoteIn):
    """Neues Angebot (Status ENTWURF) mit Positionen anlegen."""
    actor, _ = require(request, "invoicing", "ANLEGEN")
    try:
        quote = beleg_service.create_quote(
            actor,
            property_id=payload.property_id,
            title=payload.title,
            project_id=payload.project_id,
            quote_date=payload.quote_date,
            valid_until_date=payload.valid_until_date,
            lines=[line.dict() for line in payload.lines],
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _quote_detail(quote.id))


@router.post("/quotes/{quote_id}/send", response=QuoteDetailOut, auth=django_auth)
def send_quote(request, quote_id: UUID):
    """Angebot versenden (ENTWURF → … → VERSENDET); DB vergibt die AN-Nummer und
    friert den Beleg ein."""
    actor, _ = require(request, "invoicing", "VERSENDEN")
    try:
        beleg_service.send_quote(actor, quote_id=quote_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _quote_detail(quote_id)


@router.get("/quotes/{quote_id}", response=QuoteDetailOut)
def get_quote(request, quote_id: UUID):
    """Detail eines Angebots inkl. Positionen."""
    require(request, "invoicing", "LESEN")
    return _quote_detail(quote_id)


# --- Rechnungen (invoicing.invoice) ----------------------------------------

class InvoiceOut(Schema):
    id: UUID
    invoice_number: str | None = None
    invoice_type: str
    status: str
    currency: str
    invoice_date: date | None = None
    net_total: Decimal | None = None
    gross_total: Decimal | None = None
    property: PropertyRefOut


class InvoiceListOut(Schema):
    items: list[InvoiceOut]
    total: int
    page: int
    page_size: int


class InvoicePartyOut(Schema):
    party_id: UUID
    display_name: str
    role: str
    is_primary: bool
    allocation_percent: Decimal | None = None


class InvoiceDetailOut(InvoiceOut):
    due_date: date | None = None
    tax_total: Decimal | None = None
    version: int
    project: ProjectRefOut | None = None
    work_order_number: str | None = None
    published_at: datetime | None = None
    has_snapshot: bool = False
    content_hash: str | None = None
    parties: list[InvoicePartyOut] = []
    lines: list[QuoteLineOut]


class InvoiceIn(Schema):
    property_id: UUID
    invoice_type: str = "RECHNUNG"
    project_id: UUID | None = None
    work_order_id: UUID | None = None
    reference_invoice_id: UUID | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    lines: list[QuoteLineIn] = []


class InvoicePartyIn(Schema):
    party_id: UUID
    role: str
    is_primary: bool = False
    allocation_percent: Decimal | None = None
    liability_group: str | None = None
    liability_basis: str | None = None


class InvoiceFilter(Schema):
    q: str | None = None
    status: str | None = None
    invoice_type: str | None = None
    property_id: UUID | None = None
    project_id: UUID | None = None


def _invoice_out(invoice):
    return InvoiceOut(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_type=invoice.invoice_type,
        status=invoice.status,
        currency=invoice.currency,
        invoice_date=invoice.invoice_date,
        net_total=invoice.net_total,
        gross_total=invoice.gross_total,
        property=_property_ref(invoice),
    )


@router.get("/invoices", response=InvoiceListOut)
def list_invoices(
    request,
    filters: InvoiceFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Rechnungen auflisten: Suche (Nummer), Status-/Typ-/Objekt-/Projektfilter."""
    require(request, "invoicing", "LESEN")
    qs = Invoice.objects.select_related("property__address")
    if filters.q:
        qs = qs.filter(invoice_number__icontains=filters.q.strip())
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.invoice_type:
        qs = qs.filter(invoice_type=filters.invoice_type)
    if filters.property_id:
        qs = qs.filter(property_id=filters.property_id)
    if filters.project_id:
        qs = qs.filter(project_id=filters.project_id)
    qs = qs.order_by("-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_invoice_out(i) for i in qs[start:start + page_size]]
    return InvoiceListOut(items=items, total=total, page=page, page_size=page_size)


def _invoice_detail(invoice_id):
    invoice = (
        Invoice.objects.filter(id=invoice_id)
        .select_related("property__address", "project", "work_order")
        .prefetch_related("lines", "parties__party")
        .first()
    )
    if invoice is None:
        raise HttpError(404, "Rechnung nicht gefunden.")

    lines = [
        QuoteLineOut(
            position_number=l.position_number,
            line_type=l.line_type,
            description=l.description,
            quantity=l.quantity,
            unit=l.unit,
            unit_price=l.unit_price,
            discount_percent=l.discount_percent,
            tax_code=l.tax_code_id,
            tax_rate_percent=l.tax_rate_percent,
            net_amount=l.net_amount,
        )
        for l in sorted(invoice.lines.all(), key=lambda l: l.position_number)
    ]
    parties = [
        InvoicePartyOut(
            party_id=p.party.id,
            display_name=p.party.display_name,
            role=p.role,
            is_primary=p.is_primary,
            allocation_percent=p.allocation_percent,
        )
        for p in sorted(invoice.parties.all(), key=lambda p: (p.role, not p.is_primary))
    ]
    project = (
        ProjectRefOut(
            id=invoice.project.id,
            project_number=invoice.project.project_number,
            name=invoice.project.name,
        )
        if invoice.project_id
        else None
    )
    return InvoiceDetailOut(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_type=invoice.invoice_type,
        status=invoice.status,
        currency=invoice.currency,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        net_total=invoice.net_total,
        tax_total=invoice.tax_total,
        gross_total=invoice.gross_total,
        version=invoice.version,
        property=_property_ref(invoice),
        project=project,
        work_order_number=(
            invoice.work_order.order_number if invoice.work_order_id else None
        ),
        published_at=invoice.published_at,
        has_snapshot=invoice.billing_snapshot is not None,
        content_hash=invoice.content_hash,
        parties=parties,
        lines=lines,
    )


@router.post("/invoices", response={201: InvoiceDetailOut}, auth=django_auth)
def create_invoice(request, payload: InvoiceIn):
    """Neue Rechnung/Gutschrift (Status ENTWURF) mit Positionen anlegen."""
    actor, _ = require(request, "invoicing", "ANLEGEN")
    try:
        invoice = beleg_service.create_invoice(
            actor,
            property_id=payload.property_id,
            invoice_type=payload.invoice_type,
            project_id=payload.project_id,
            work_order_id=payload.work_order_id,
            reference_invoice_id=payload.reference_invoice_id,
            invoice_date=payload.invoice_date,
            due_date=payload.due_date,
            lines=[line.dict() for line in payload.lines],
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _invoice_detail(invoice.id))


@router.post(
    "/invoices/{invoice_id}/parties",
    response={201: InvoiceDetailOut},
    auth=django_auth,
)
def add_invoice_party(request, invoice_id: UUID, payload: InvoicePartyIn):
    """Rechnungsbeteiligten (Schuldner/Empfänger …) hinzufügen (nur im Entwurf)."""
    # Beteiligten am Entwurf ergänzen = Änderung am Beleg → AENDERN.
    actor, _ = require(request, "invoicing", "AENDERN")
    try:
        beleg_service.add_invoice_party(
            actor,
            invoice_id=invoice_id,
            party_id=payload.party_id,
            role=payload.role,
            is_primary=payload.is_primary,
            allocation_percent=payload.allocation_percent,
            liability_group=payload.liability_group,
            liability_basis=payload.liability_basis,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _invoice_detail(invoice_id))


@router.post("/invoices/{invoice_id}/publish", response=InvoiceDetailOut, auth=django_auth)
def publish_invoice(request, invoice_id: UUID):
    """Rechnung veröffentlichen (ENTWURF → VEROEFFENTLICHT); DB vergibt die Nummer
    und prüft die Tore (Auftrag geprüft, Schuldner/Empfänger)."""
    # Veröffentlichen ist das Freigabetor der Rechnung → FREIGEBEN.
    actor, _ = require(request, "invoicing", "FREIGEBEN")
    try:
        beleg_service.publish_invoice(actor, invoice_id=invoice_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _invoice_detail(invoice_id)


@router.get("/invoices/{invoice_id}", response=InvoiceDetailOut)
def get_invoice(request, invoice_id: UUID):
    """Detail einer Rechnung inkl. Positionen."""
    require(request, "invoicing", "LESEN")
    return _invoice_detail(invoice_id)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(request, invoice_id: UUID):
    """PDF-Ausfertigung einer veröffentlichten Rechnung (on-the-fly gerendert).

    Nur festgeschriebene Belege (VEROEFFENTLICHT) erhalten eine Ausfertigung; für
    Entwürfe/unbekannte Belege → 404."""
    require(request, "invoicing", "LESEN")
    pdf = beleg_pdf_service.render_invoice_pdf(invoice_id)
    if pdf is None:
        raise HttpError(404, "Veröffentlichte Rechnung nicht gefunden.")
    invoice = Invoice.objects.filter(id=invoice_id).only("invoice_number").first()
    raw = invoice.invoice_number or str(invoice_id)
    # Dateinamen auf unbedenkliche Zeichen beschränken (Defense-in-Depth gegen
    # Header-Injection; Belegnummern sind ohnehin RE-/GS-Format).
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{safe or "beleg"}.pdf"'
    return response
