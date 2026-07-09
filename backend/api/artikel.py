"""Artikel-API — Artikel (pricing.article) und Leistungen (pricing.assembly).

Dieser Slice ist lesend (Liste/Detail). Anlegen/Preise/Kalkulation folgen als
eigener Slice (u. a. fehlt eine DB-Nummernautomatik; VK ist eine Formel).
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from db_core.models import Article, Assembly
from db_core.services import kalkulation as kalkulation_service

router = Router()


# --- Artikel-Schemas -------------------------------------------------------

class ArticleOut(Schema):
    id: UUID
    article_number: str
    description: str
    unit: str
    line_type: str
    status: str
    list_price: Decimal | None = None


class ArticleListOut(Schema):
    items: list[ArticleOut]
    total: int
    page: int
    page_size: int


class ArticleDetailOut(ArticleOut):
    long_description: str | None = None
    gtin: str | None = None
    manufacturer_name: str | None = None
    manufacturer_number: str | None = None
    product_group: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class ArticleFilter(Schema):
    q: str | None = None
    line_type: str | None = None
    status: str | None = None


# --- Leistungs-Schemas -----------------------------------------------------

class AssemblyOut(Schema):
    id: UUID
    assembly_number: str
    name: str
    unit: str
    status: str


class AssemblyListOut(Schema):
    items: list[AssemblyOut]
    total: int
    page: int
    page_size: int


class ComponentOut(Schema):
    position: int
    kind: str  # MATERIAL | LOHN
    description: str
    quantity: Decimal | None = None
    unit: str | None = None  # Materialeinheit (aus dem Artikel)
    minutes: Decimal | None = None


class AssemblyDetailOut(AssemblyOut):
    internal_name: str | None = None
    description: str | None = None
    version: int
    components: list[ComponentOut]


class AssemblyFilter(Schema):
    q: str | None = None
    status: str | None = None


# --- Artikel-Endpoints -----------------------------------------------------

@router.get("/articles", response=ArticleListOut)
def list_articles(
    request,
    filters: ArticleFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Artikel auflisten: Suche (Nummer/Beschreibung), Typ-/Statusfilter."""
    qs = Article.objects.all()
    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(
            Q(article_number__icontains=needle) | Q(description__icontains=needle)
        )
    if filters.line_type:
        qs = qs.filter(line_type=filters.line_type)
    if filters.status:
        qs = qs.filter(status=filters.status)
    qs = qs.order_by("article_number", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = list(qs[start:start + page_size])
    return ArticleListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/articles/{article_id}", response=ArticleDetailOut)
def get_article(request, article_id: UUID):
    article = Article.objects.filter(id=article_id).first()
    if article is None:
        raise HttpError(404, "Artikel nicht gefunden.")
    return article


class KalkulationVariantOut(Schema):
    label: str
    is_standard: bool
    kind: str  # FORMEL | FESTPREIS
    group_name: str | None = None
    basis_kind: str | None = None  # EK | LISTENPREIS
    basis_amount: str | None = None
    operator: str | None = None  # AUFSCHLAG | ABSCHLAG
    percent_change: str | None = None
    amount_change: str | None = None
    sale_price: str | None = None


class KalkulationOut(Schema):
    article_id: UUID
    article_number: str
    description: str
    list_price: str | None = None
    ek: str | None = None
    variants: list[KalkulationVariantOut]


@router.get("/articles/{article_id}/kalkulation", response=KalkulationOut)
def article_kalkulation(request, article_id: UUID):
    """VK-Kalkulation eines Artikels: Listenpreis, aktueller EK und die
    VK-Varianten (Formel oder Festpreis) mit errechnetem Verkaufspreis."""
    data = kalkulation_service.article_kalkulation(article_id)
    if data is None:
        raise HttpError(404, "Artikel nicht gefunden.")
    return data


# --- Leistungs-Endpoints ---------------------------------------------------

@router.get("/assemblies", response=AssemblyListOut)
def list_assemblies(
    request,
    filters: AssemblyFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Leistungen auflisten: Suche (Nummer/Name), Statusfilter."""
    qs = Assembly.objects.all()
    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(
            Q(assembly_number__icontains=needle) | Q(name__icontains=needle)
        )
    if filters.status:
        qs = qs.filter(status=filters.status)
    qs = qs.order_by("assembly_number", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = list(qs[start:start + page_size])
    return AssemblyListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/assemblies/{assembly_id}", response=AssemblyDetailOut)
def get_assembly(request, assembly_id: UUID):
    assembly = (
        Assembly.objects.filter(id=assembly_id)
        .prefetch_related("components__article", "components__wage_group")
        .first()
    )
    if assembly is None:
        raise HttpError(404, "Leistung nicht gefunden.")

    components = []
    for c in sorted(assembly.components.all(), key=lambda c: c.position):
        if c.article_id:
            components.append(
                ComponentOut(
                    position=c.position,
                    kind="MATERIAL",
                    description=c.article.description,
                    quantity=c.quantity,
                    unit=c.article.unit,
                )
            )
        else:
            components.append(
                ComponentOut(
                    position=c.position,
                    kind="LOHN",
                    description=c.wage_group.name,
                    minutes=c.minutes,
                )
            )
    return AssemblyDetailOut(
        id=assembly.id,
        assembly_number=assembly.assembly_number,
        name=assembly.name,
        unit=assembly.unit,
        status=assembly.status,
        internal_name=assembly.internal_name,
        description=assembly.description,
        version=assembly.version,
        components=components,
    )
