"""Artikel-API — Artikel (pricing.article) und Leistungen (pricing.assembly).

Dieser Slice ist lesend (Liste/Detail). Anlegen/Preise/Kalkulation folgen als
eigener Slice (u. a. fehlt eine DB-Nummernautomatik; VK ist eine Formel).
"""
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.db.models import Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_create
from db_core.models import (
    Article,
    ArticleSalePrice,
    Assembly,
    SalePriceGroup,
    SupplierConnection,
    WageGroup,
)
from db_core.services import artikel as artikel_service
from db_core.services import kalkulation as kalkulation_service

router = Router()


def _quantize(value, places):
    """Betrag/Menge auf die DB-Spaltenskala runden (GoBD: ROUND_HALF_UP).

    Vor dem Schreiben angewandt, damit Django genauso rundet wie der DB-CHECK
    (sonst 500 statt kontrolliertem Wert). None bleibt None.
    """
    if value is None:
        return None
    # Decimal(str(value)) statt Decimal(value): rutschte je ein float durch,
    # erbte der Decimal dessen Binärfehler. Muster wie beleg.py::_prepare_lines.
    return Decimal(str(value)).quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
    )


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
    manufacturer_type: str | None = None
    product_group: str | None = None
    matchcode: str | None = None
    min_order_quantity: Decimal | None = None
    quantity_step: Decimal | None = None
    delivery_time_days: int | None = None
    tax_code: str | None = None
    cost_center_id: UUID | None = None
    cost_center_label: str | None = None
    price_unit: int = 1
    # Primärer Lieferantenbezug (Hero-Reiter „Informationen").
    supplier_party_id: UUID | None = None
    supplier_name: str | None = None
    supplier_article_number: str | None = None
    last_purchase_price: Decimal | None = None
    version: int
    created_at: datetime
    updated_at: datetime


def _article_detail_out(article) -> "ArticleDetailOut":
    """Artikel-Detail inkl. abgeleitetem primären Lieferantenbezug und
    Kostenstellen-Bezeichnung."""
    ref = kalkulation_service.primary_supplier_reference(article.id)
    cc = article.cost_center if article.cost_center_id else None
    return ArticleDetailOut(
        id=article.id,
        article_number=article.article_number,
        description=article.description,
        unit=article.unit,
        line_type=article.line_type,
        status=article.status,
        list_price=article.list_price,
        long_description=article.long_description,
        gtin=article.gtin,
        manufacturer_name=article.manufacturer_name,
        manufacturer_number=article.manufacturer_number,
        manufacturer_type=article.manufacturer_type,
        product_group=article.product_group,
        matchcode=article.matchcode,
        min_order_quantity=article.min_order_quantity,
        quantity_step=article.quantity_step,
        delivery_time_days=article.delivery_time_days,
        tax_code=article.tax_code_id,
        cost_center_id=article.cost_center_id,
        cost_center_label=(f"{cc.code} — {cc.label}" if cc else None),
        price_unit=article.price_unit,
        supplier_party_id=(ref.supplier_party_id if ref else None),
        supplier_name=(ref.supplier_party.display_name if ref else None),
        supplier_article_number=(ref.supplier_article_number if ref else None),
        last_purchase_price=(ref.last_purchase_price if ref else None),
        version=article.version,
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


class ArticleFilter(Schema):
    q: str | None = None
    line_type: str | None = None
    status: str | None = None
    # Bezugsquelle: GROSSHAENDLER | HERSTELLER | ALLE (Standard: ALLE).
    #
    # Der Artikelstamm führt beides gemeinsam: den Bestellkatalog des
    # Großhändlers UND die Herstellerkataloge, aus denen der Gerätefinder
    # Ersatzteile zu einer Thermen-Typenbezeichnung sucht. Im Angebot darf
    # letzteres nicht auftauchen — ein Vaillant-Mikroschalter ist beim
    # Großhändler nicht bestellbar. Der Angebotseditor fragt deshalb
    # `bezugsquelle=GROSSHAENDLER` an.
    #
    # Eigene Artikel (ohne Lieferantenreferenz) gelten immer als beschaffbar und
    # bleiben in beiden Sichten enthalten.
    bezugsquelle: str | None = None


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


BEZUGSQUELLEN = ("GROSSHAENDLER", "HERSTELLER", "ALLE")


def _nach_bezugsquelle(qs, bezugsquelle):
    """Filtert Artikel nach der Art ihrer Bezugsquelle.

    Die Zuordnung steht auf `pricing.supplier_connection.connection_kind`, nicht
    am Artikel: derselbe Namensraum kann viele Artikel tragen, und die Einordnung
    (Großhändler vs. Hersteller) gehört zur Anbindung.

    Artikel OHNE Lieferantenreferenz sind eigene Artikel (Pauschalen, Fahrtkosten,
    selbst angelegtes Material). Sie bleiben in jeder Sicht enthalten — sie
    verschwänden sonst aus dem Angebot, obwohl sie dort hingehören.
    """
    if not bezugsquelle or bezugsquelle == "ALLE":
        return qs
    if bezugsquelle not in BEZUGSQUELLEN:
        raise HttpError(
            422,
            f"Unbekannte Bezugsquelle '{bezugsquelle}'. "
            f"Erlaubt: {', '.join(BEZUGSQUELLEN)}.",
        )
    passende_namespaces = SupplierConnection.objects.filter(
        connection_kind=bezugsquelle, status="ACTIVE"
    ).values_list("source_namespace", flat=True)
    return qs.filter(
        Q(supplier_references__source_namespace__in=list(passende_namespaces))
        | Q(supplier_references__isnull=True)
    ).distinct()


# --- Artikel-Endpoints -----------------------------------------------------

@router.get("/articles", response=ArticleListOut)
def list_articles(
    request,
    filters: ArticleFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Artikel auflisten: Suche (Nummer/Beschreibung), Typ-/Statusfilter.

    Standardmäßig nur AKTIVE Artikel. Ein Artikel wird nie gelöscht (GoBD,
    `trg_article_no_delete`), sondern auf INAKTIV gesetzt — er darf dann aber auch
    nicht mehr in der Suche auftauchen, sonst landet ausrangiertes Material
    wieder im Angebot. Wer ihn ausdrücklich sehen will, fragt `status=INAKTIV`
    oder `status=ALLE` an.
    """
    require(request, "pricing", "LESEN")
    qs = Article.objects.all()
    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(
            Q(article_number__icontains=needle) | Q(description__icontains=needle)
        )
    if filters.line_type:
        qs = qs.filter(line_type=filters.line_type)
    if filters.status == "ALLE":
        pass
    elif filters.status:
        qs = qs.filter(status=filters.status)
    else:
        qs = qs.filter(status="AKTIV")
    qs = _nach_bezugsquelle(qs, filters.bezugsquelle)
    qs = qs.order_by("article_number", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = list(qs[start:start + page_size])
    return ArticleListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/articles/{article_id}", response=ArticleDetailOut)
def get_article(request, article_id: UUID):
    require(request, "pricing", "LESEN")
    article = (
        Article.objects.select_related("cost_center", "tax_code")
        .filter(id=article_id)
        .first()
    )
    if article is None:
        raise HttpError(404, "Artikel nicht gefunden.")
    return _article_detail_out(article)


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
    # Zweifelsfall: das Wort „Kalkulation" steht in der Aktionsliste unter AENDERN,
    # meint dort aber das SCHREIBEN einer Kalkulation. Dieser Endpunkt ist ein GET,
    # der nur liest/ableitet und nichts schreibt → LESEN.
    require(request, "pricing", "LESEN")
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
    require(request, "pricing", "LESEN")
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


def _assembly_detail(assembly_id):
    """Leistungs-Detail inkl. Stückliste; 404 wenn nicht vorhanden."""
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


@router.get("/assemblies/{assembly_id}", response=AssemblyDetailOut)
def get_assembly(request, assembly_id: UUID):
    require(request, "pricing", "LESEN")
    return _assembly_detail(assembly_id)


# --- Schreibende Endpoints (Session-Auth Pflicht) --------------------------
# row_scope: Das Modul `pricing` kennt keine Rolle mit Scope 'EIGENE' (nur
# workflow: Monteur). Die erzeugten Zeilen (Artikel/Leistung/Lohngruppe/
# VK-Gruppe/VK-Variante) tragen ohnehin kein Owner-Feld. ANLEGEN daher über
# `require_create`, das Preis-Update (AENDERN) über `require` (fail-closed).

class WageGroupOut(Schema):
    id: UUID
    name: str
    kind: str
    hourly_rate: Decimal
    cost_rate: Decimal | None = None
    status: str


class WageGroupIn(Schema):
    name: str
    hourly_rate: Decimal
    kind: str = "LOHN"
    cost_rate: Decimal | None = None


class ArticleIn(Schema):
    article_number: str
    description: str
    unit: str
    line_type: str = "MATERIAL"
    list_price: Decimal | None = None
    long_description: str | None = None
    manufacturer_name: str | None = None
    manufacturer_number: str | None = None
    manufacturer_type: str | None = None
    product_group: str | None = None
    matchcode: str | None = None
    min_order_quantity: Decimal | None = None
    quantity_step: Decimal | None = None
    delivery_time_days: int | None = None
    tax_code: str | None = None
    cost_center_id: UUID | None = None
    price_unit: int | None = None


class ComponentIn(Schema):
    article_id: UUID | None = None
    quantity: Decimal | None = None
    wage_group_id: UUID | None = None
    minutes: Decimal | None = None
    note: str | None = None


class AssemblyIn(Schema):
    assembly_number: str
    name: str
    unit: str
    description: str | None = None
    components: list[ComponentIn] = []


class SalePriceGroupOut(Schema):
    id: UUID
    name: str
    calc_basis: str
    operator: str
    percent_change: Decimal | None = None
    amount_change: Decimal | None = None
    status: str


class SalePriceGroupIn(Schema):
    name: str
    calc_basis: str = "EK"
    operator: str = "AUFSCHLAG"
    percent_change: Decimal | None = None
    amount_change: Decimal | None = None


class ArticleSalePriceOut(Schema):
    id: UUID
    label: str
    sale_price_group_id: UUID | None = None
    fixed_price: Decimal | None = None
    is_standard: bool


class ArticleSalePriceIn(Schema):
    label: str = "Standard"
    sale_price_group_id: UUID | None = None
    fixed_price: Decimal | None = None
    is_standard: bool = False


@router.post("/articles", response={201: ArticleDetailOut}, auth=django_auth)
def create_article(request, payload: ArticleIn):
    """Artikel anlegen (Status AKTIV). article_number ist nutzergesetzt (keine
    DB-Automatik)."""
    actor = require_create(request, "pricing", "ANLEGEN")
    try:
        article = artikel_service.create_article(
            actor,
            article_number=payload.article_number,
            description=payload.description,
            unit=payload.unit,
            line_type=payload.line_type,
            list_price=_quantize(payload.list_price, 4),   # numeric(15,4) seit Migration 0039
            long_description=payload.long_description,
            manufacturer_name=payload.manufacturer_name,
            manufacturer_number=payload.manufacturer_number,
            manufacturer_type=payload.manufacturer_type,
            product_group=payload.product_group,
            matchcode=payload.matchcode,
            min_order_quantity=_quantize(payload.min_order_quantity, 3),
            quantity_step=_quantize(payload.quantity_step, 3),
            delivery_time_days=payload.delivery_time_days,
            tax_code=payload.tax_code,
            cost_center_id=payload.cost_center_id,
            price_unit=payload.price_unit,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _article_detail_out(Article.objects.get(id=article.id)))


@router.post("/assemblies", response={201: AssemblyDetailOut}, auth=django_auth)
def create_assembly(request, payload: AssemblyIn):
    """Leistung mit Stückliste anlegen. Jede Position ist entweder Material
    (article_id + quantity) ODER Lohn (wage_group_id + minutes)."""
    actor = require_create(request, "pricing", "ANLEGEN")
    components = [
        {
            "article_id": c.article_id,
            "quantity": _quantize(c.quantity, 3),
            "wage_group_id": c.wage_group_id,
            "minutes": _quantize(c.minutes, 2),
            "note": c.note,
        }
        for c in payload.components
    ]
    try:
        assembly = artikel_service.create_assembly(
            actor,
            assembly_number=payload.assembly_number,
            name=payload.name,
            unit=payload.unit,
            description=payload.description,
            components=components,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _assembly_detail(assembly.id))


@router.post("/wage_groups", response={201: WageGroupOut}, auth=django_auth)
def create_wage_group(request, payload: WageGroupIn):
    """Lohn-/Maschinengruppe anlegen (Status AKTIV)."""
    actor = require_create(request, "pricing", "ANLEGEN")
    try:
        wg = artikel_service.create_wage_group(
            actor,
            name=payload.name,
            hourly_rate=_quantize(payload.hourly_rate, 2),
            kind=payload.kind,
            cost_rate=_quantize(payload.cost_rate, 2),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, WageGroup.objects.get(id=wg.id))


@router.post("/sale_price_groups", response={201: SalePriceGroupOut}, auth=django_auth)
def create_sale_price_group(request, payload: SalePriceGroupIn):
    """VK-Kalkulationsgruppe anlegen. Genau eines von percent_change ODER
    amount_change ist zu setzen (DB-XOR)."""
    actor = require_create(request, "pricing", "ANLEGEN")
    try:
        group = artikel_service.create_sale_price_group(
            actor,
            name=payload.name,
            calc_basis=payload.calc_basis,
            operator=payload.operator,
            percent_change=_quantize(payload.percent_change, 3),
            amount_change=_quantize(payload.amount_change, 2),
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, SalePriceGroup.objects.get(id=group.id))


@router.put(
    "/articles/{article_id}/sale_price", response=ArticleSalePriceOut, auth=django_auth
)
def set_article_sale_price(request, article_id: UUID, payload: ArticleSalePriceIn):
    """VK-Variante eines Artikels setzen (Formel-Gruppe ODER Festpreis).

    Torfunktion `require` (AENDERN): der Endpunkt wertet keinen row_scope aus;
    `pricing` kennt ohnehin keine 'EIGENE'-Rolle.
    """
    actor, _ = require(request, "pricing", "AENDERN")
    if not Article.objects.filter(id=article_id).exists():
        raise HttpError(404, "Artikel nicht gefunden.")
    try:
        asp = artikel_service.set_article_sale_price(
            actor,
            article_id=article_id,
            label=payload.label,
            sale_price_group_id=payload.sale_price_group_id,
            fixed_price=_quantize(payload.fixed_price, 2),
            is_standard=payload.is_standard,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return ArticleSalePrice.objects.get(id=asp.id)


# --- Stammdaten-Listen (Auswahllisten für Schreib-UIs) ---------------------
# Lohn- und VK-Preisgruppen als schlanke Auswahllisten für die Leistungs-
# Stückliste (Lohnpositionen) bzw. den VK-Formelpreis (Preisgruppen-Wahl).
# Recht `pricing`/LESEN, `require` (fail-closed): `pricing` kennt keine
# 'EIGENE'-Rolle, ein Scope-Konflikt kann hier also gar nicht auftreten.

@router.get("/wage_groups", response=list[WageGroupOut])
def list_wage_groups(request, status: str | None = Query(None)):
    """Lohn-/Maschinengruppen auflisten (Standard: nur AKTIV). status-Filter
    optional (z. B. INAKTIV zum Aufräumen)."""
    require(request, "pricing", "LESEN")
    qs = WageGroup.objects.all()
    qs = qs.filter(status=status) if status else qs.filter(status="AKTIV")
    return list(qs.order_by("name", "id"))


@router.get("/sale_price_groups", response=list[SalePriceGroupOut])
def list_sale_price_groups(request, status: str | None = Query(None)):
    """VK-Kalkulationsgruppen auflisten (Standard: nur AKTIV). status-Filter
    optional."""
    require(request, "pricing", "LESEN")
    qs = SalePriceGroup.objects.all()
    qs = qs.filter(status=status) if status else qs.filter(status="AKTIV")
    return list(qs.order_by("name", "id"))


class AssemblyComponentsIn(Schema):
    components: list[ComponentIn]


@router.post(
    "/assemblies/{assembly_id}/components", response=AssemblyDetailOut, auth=django_auth
)
def add_assembly_components(request, assembly_id: UUID, payload: AssemblyComponentsIn):
    """Fügt einer bestehenden Leistung Positionen hinzu (Recht `pricing`/AENDERN).

    Die Stückliste ist nach der Anlage änderbar: pricing.assembly_component trägt
    keinen Unveränderlichkeits-Trigger (anders als eingefrorene Belegpositionen).
    Neue Positionen werden hinten angehängt; die FKs (Artikel/Lohngruppe) prüft
    der Service vorab (klarer 422 statt DB-IntegrityError). Antwort: die
    aktualisierte Leistung inkl. vollständiger Stückliste.
    """
    actor, _ = require(request, "pricing", "AENDERN")
    if not Assembly.objects.filter(id=assembly_id).exists():
        raise HttpError(404, "Leistung nicht gefunden.")
    components = [
        {
            "article_id": c.article_id,
            "quantity": _quantize(c.quantity, 3),
            "wage_group_id": c.wage_group_id,
            "minutes": _quantize(c.minutes, 2),
            "note": c.note,
        }
        for c in payload.components
    ]
    try:
        artikel_service.add_assembly_components(
            actor, assembly_id=assembly_id, components=components
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _assembly_detail(assembly_id)


# --- Artikel bearbeiten, Historie, Stammdaten-Übernahme ---------------------

class ArticleUpdateIn(Schema):
    """Nur gesetzte Felder werden geändert (exclude_unset)."""
    article_number: str | None = None
    description: str | None = None
    long_description: str | None = None
    unit: str | None = None
    line_type: str | None = None
    list_price: Decimal | None = None
    gtin: str | None = None
    manufacturer_name: str | None = None
    manufacturer_number: str | None = None
    manufacturer_type: str | None = None
    product_group: str | None = None
    matchcode: str | None = None
    min_order_quantity: Decimal | None = None
    quantity_step: Decimal | None = None
    delivery_time_days: int | None = None
    tax_code: str | None = None
    cost_center_id: UUID | None = None
    price_unit: int | None = None


@router.put("/articles/{article_id}", response=ArticleDetailOut, auth=django_auth)
def update_article(request, article_id: UUID, payload: ArticleUpdateIn):
    """Artikelstammdaten ändern. Jede Änderung wird auditiert (Historie-Reiter)."""
    actor, _ = require(request, "pricing", "AENDERN")
    felder = payload.model_dump(exclude_unset=True)
    if "list_price" in felder:
        felder["list_price"] = _quantize(felder["list_price"], 4)
    for feld in ("min_order_quantity", "quantity_step"):
        if feld in felder:
            felder[feld] = _quantize(felder[feld], 3)
    try:
        artikel_service.update_article(actor, article_id=article_id, **felder)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _article_detail_out(Article.objects.get(id=article_id))


class ArticleStatusIn(Schema):
    status: str


@router.post("/articles/{article_id}/status", response=ArticleDetailOut, auth=django_auth)
def set_article_status(request, article_id: UUID, payload: ArticleStatusIn):
    """Artikel aktivieren/deaktivieren. Es gibt kein Löschen (append-only)."""
    actor, _ = require(request, "pricing", "AENDERN")
    try:
        artikel_service.set_article_status(
            actor, article_id=article_id, status=payload.status
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _article_detail_out(Article.objects.get(id=article_id))


class HistorieFeldOut(Schema):
    feld: str
    vorher: str | None = None
    nachher: str | None = None


class HistorieEintragOut(Schema):
    occurred_at: datetime
    action: str
    akteur: str | None = None
    felder: list[HistorieFeldOut]


@router.get("/articles/{article_id}/historie", response=list[HistorieEintragOut])
def article_historie(request, article_id: UUID, limit: int = Query(50, ge=1, le=200)):
    """Änderungsverlauf eines Artikels aus der Audit-Spur (Reiter „Historie")."""
    require(request, "pricing", "LESEN")
    eintraege = artikel_service.article_historie(article_id, limit=limit)
    return [
        HistorieEintragOut(
            occurred_at=e["occurred_at"],
            action=e["action"],
            akteur=e["akteur"],
            felder=[
                HistorieFeldOut(
                    feld=f["feld"],
                    vorher=None if f["vorher"] is None else str(f["vorher"]),
                    nachher=None if f["nachher"] is None else str(f["nachher"]),
                )
                for f in e["felder"]
            ],
        )
        for e in eintraege
    ]


class StammdatenUebernahmeIn(Schema):
    """Werte aus einer Belegposition, die in den Artikelstamm sollen.

    Der Einkaufspreis fehlt hier absichtlich: er ist die Aussage des Händlers
    (DATANORM-Import), keine Meinung des Angebotsschreibers.
    """
    description: str | None = None
    long_description: str | None = None
    unit: str | None = None
    verkaufspreis: Decimal | None = None


@router.post(
    "/articles/{article_id}/stammdaten-uebernehmen",
    response=ArticleDetailOut,
    auth=django_auth,
)
def stammdaten_uebernehmen(request, article_id: UUID, payload: StammdatenUebernahmeIn):
    """Überträgt Werte aus einer Belegposition in den Artikelstamm.

    Das ist der Vorgang hinter dem Häkchen „Änderungen an Stammdaten übernehmen"
    im Beleg-Editor. Er läuft NIE automatisch beim Speichern eines Belegs, sondern
    nur auf ausdrückliche Anforderung — und er verlangt `pricing/AENDERN`. Wer ein
    Angebot schreiben darf (`invoicing/AENDERN`), darf damit nicht den
    Artikelstamm umschreiben, den alle anderen Angebote mitbenutzen.
    """
    actor, _ = require(request, "pricing", "AENDERN")
    daten = payload.model_dump(exclude_unset=True)
    verkaufspreis = daten.pop("verkaufspreis", None)
    try:
        artikel_service.positionswerte_in_stammdaten(
            actor,
            article_id=article_id,
            verkaufspreis=_quantize(verkaufspreis, 2),
            **daten,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _article_detail_out(Article.objects.get(id=article_id))


# --- Verkaufspreis-Tabelle (Hero-Reiter „Kalkulation" rechts) --------------

class VerkaufspreisGruppeOut(Schema):
    sale_price_group_id: UUID
    name: str
    calc_basis: str          # EK | LISTENPREIS
    operator: str            # AUFSCHLAG | ABSCHLAG
    percent_change: str | None = None
    amount_change: str | None = None
    basis_amount: str | None = None          # je Stück (Basis / price_unit)
    computed_sale_price: str | None = None   # errechneter VK je Stück
    override_price: str | None = None        # manuelle Überschreibung, wenn gesetzt
    effective_sale_price: str | None = None  # Überschreibung sonst errechnet
    is_standard: bool


class VerkaufspreiseOut(Schema):
    article_id: UUID
    article_number: str
    description: str
    unit: str
    price_unit: int
    list_price: str | None = None
    ek: str | None = None
    groups: list[VerkaufspreisGruppeOut]


@router.get("/articles/{article_id}/verkaufspreise", response=VerkaufspreiseOut)
def get_verkaufspreise(request, article_id: UUID):
    """Alle aktiven VK-Gruppen mit errechnetem/überschriebenem VK je Stück und
    der Standard-Markierung (Hero-Reiter „Verkaufspreise")."""
    require(request, "pricing", "LESEN")
    data = kalkulation_service.verkaufspreise_uebersicht(article_id)
    if data is None:
        raise HttpError(404, "Artikel nicht gefunden.")
    return data


class VerkaufspreisEintragIn(Schema):
    sale_price_group_id: UUID
    fixed_price: Decimal | None = None
    is_standard: bool = False


class VerkaufspreiseIn(Schema):
    entries: list[VerkaufspreisEintragIn]


@router.put("/articles/{article_id}/verkaufspreise", response=VerkaufspreiseOut, auth=django_auth)
def set_verkaufspreise(request, article_id: UUID, payload: VerkaufspreiseIn):
    """Setzt die ganze VK-Gruppen-Tabelle auf einmal (genau eine Standard-Gruppe).

    Recht `pricing/AENDERN`. Fremdschlüssel/Werte prüft der Service vorab
    (klarer 422 statt 500)."""
    actor, _ = require(request, "pricing", "AENDERN")
    if not Article.objects.filter(id=article_id).exists():
        raise HttpError(404, "Artikel nicht gefunden.")
    entries = [
        {
            "sale_price_group_id": e.sale_price_group_id,
            "fixed_price": _quantize(e.fixed_price, 2),  # numeric(12,2)
            "is_standard": e.is_standard,
        }
        for e in payload.entries
    ]
    try:
        artikel_service.set_verkaufspreise(actor, article_id=article_id, entries=entries)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return kalkulation_service.verkaufspreise_uebersicht(article_id)


# --- Primärer Lieferantenbezug (Hero-Reiter „Informationen") ---------------

class LieferantIn(Schema):
    supplier_party_id: UUID
    supplier_article_number: str
    last_purchase_price: Decimal | None = None
    currency: str = "EUR"


@router.put("/articles/{article_id}/lieferant", response=ArticleDetailOut, auth=django_auth)
def set_lieferant(request, article_id: UUID, payload: LieferantIn):
    """Setzt den primären (manuellen) Lieferantenbezug eines Artikels
    (Lieferant, Lieferanten-Artikelnummer, Einkaufspreis). Recht `pricing/AENDERN`.

    Der Einkaufspreis wird je `price_unit` Einheiten gespeichert; die Umrechnung
    auf je Stück macht die VK-Kalkulation."""
    actor, _ = require(request, "pricing", "AENDERN")
    if not Article.objects.filter(id=article_id).exists():
        raise HttpError(404, "Artikel nicht gefunden.")
    try:
        artikel_service.set_primary_supplier(
            actor,
            article_id=article_id,
            supplier_party_id=payload.supplier_party_id,
            supplier_article_number=payload.supplier_article_number,
            last_purchase_price=_quantize(payload.last_purchase_price, 4),  # numeric(15,4)
            currency=payload.currency,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _article_detail_out(
        Article.objects.select_related("cost_center", "tax_code").get(id=article_id)
    )
