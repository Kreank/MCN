"""Beleg-API — Angebote (invoicing.quote) inkl. Positionen.

Lesen in der Dev-Phase ohne Auth; Anlegen verlangt Django-Session + app_user.
Deckt Anlage bis ENTWURF sowie Liste/Detail ab (Versand-Workflow folgt separat).
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db.models import Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from db_core.models import Quote
from db_core.services import beleg as beleg_service

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
        lines=lines,
    )


# --- Schreibender Endpoint (Session-Auth Pflicht) --------------------------

def _actor_id(request):
    actor = getattr(request.user, "app_user_id", None)
    if actor is None:
        raise HttpError(
            403,
            "Dem Login-Konto ist kein security.app_user zugeordnet; "
            "fachliche Schreibvorgänge sind damit nicht möglich.",
        )
    return actor


@router.post("/quotes", response={201: QuoteDetailOut}, auth=django_auth)
def create_quote(request, payload: QuoteIn):
    """Neues Angebot (Status ENTWURF) mit Positionen anlegen."""
    actor = _actor_id(request)
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


@router.get("/quotes/{quote_id}", response=QuoteDetailOut)
def get_quote(request, quote_id: UUID):
    """Detail eines Angebots inkl. Positionen."""
    return _quote_detail(quote_id)
