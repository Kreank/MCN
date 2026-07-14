"""Auftrags-API — Aufträge (workflow.work_order) inkl. Beteiligten und
Statusverlauf.

Wie die übrigen APIs: Lesen in der Dev-Phase ohne Auth, Schreiben verlangt
Django-Session + zugeordnetes app_user. Views bleiben dünn und rufen die
Service-Schicht; Model-Instanzen verlassen die API nicht.
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require
from db_core.models import StatusChange, WorkOrder
from db_core.services import abrechnung as abrechnung_service
from db_core.services import auftrag as auftrag_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class PropertyRefOut(Schema):
    id: UUID
    property_number: str
    name: str
    city: str


class ProjectRefOut(Schema):
    id: UUID
    project_number: str
    name: str


class WorkOrderOut(Schema):
    id: UUID
    order_number: str
    title: str
    status: str
    priority: str
    responsibility_scope: str
    is_emergency: bool
    desired_date: date | None = None
    created_at: datetime
    property: PropertyRefOut
    project: ProjectRefOut | None = None
    service_case_number: str | None = None


class WorkOrderListOut(Schema):
    items: list[WorkOrderOut]
    total: int
    page: int
    page_size: int


class WorkOrderPartyOut(Schema):
    party_id: UUID
    display_name: str
    role: str
    is_primary: bool
    allocation_percent: Decimal | None = None
    source: str


class StatusChangeOut(Schema):
    from_status: str | None = None
    to_status: str
    reason: str | None = None
    changed_by: str | None = None
    occurred_at: datetime


class WorkOrderDetailOut(WorkOrderOut):
    description: str | None = None
    customer_reference: str | None = None
    order_evidence_reference: str | None = None
    responsibility_confirmed_at: datetime | None = None
    # PAUSCHAL (Default) | REGIE — steuert, WORAUS die Rechnung entsteht
    # (Angebotskopie vs. Bericht + Zeiten), Migration 0084.
    billing_mode: str = "PAUSCHAL"
    version: int
    parties: list[WorkOrderPartyOut]
    history: list[StatusChangeOut]


class WorkOrderIn(Schema):
    property_id: UUID
    title: str
    project_id: UUID | None = None
    service_case_id: UUID | None = None
    description: str | None = None
    priority: str = "NORMAL"
    desired_date: date | None = None
    customer_reference: str | None = None
    is_emergency: bool = False


class WorkOrderPartyIn(Schema):
    party_id: UUID
    role: str
    is_primary: bool = False
    allocation_percent: Decimal | None = None
    source: str = "MANUAL"


class StatusIn(Schema):
    to_status: str
    reason: str | None = None


class ResponsibilityIn(Schema):
    scope: str


class EvidenceIn(Schema):
    reference: str


class WorkOrderFilter(Schema):
    q: str | None = None
    status: str | None = None
    project_id: UUID | None = None
    property_id: UUID | None = None
    service_case_id: UUID | None = None


# --- Mapper ----------------------------------------------------------------

def _property_ref(order):
    p = order.property
    return PropertyRefOut(
        id=p.id, property_number=p.property_number, name=p.name, city=p.address.city
    )


def _project_ref(order):
    if not order.project_id:
        return None
    return ProjectRefOut(
        id=order.project.id,
        project_number=order.project.project_number,
        name=order.project.name,
    )


def _work_order_out(order):
    return WorkOrderOut(
        id=order.id,
        order_number=order.order_number,
        title=order.title,
        status=order.status,
        priority=order.priority,
        responsibility_scope=order.responsibility_scope,
        is_emergency=order.is_emergency,
        desired_date=order.desired_date,
        created_at=order.created_at,
        property=_property_ref(order),
        project=_project_ref(order),
        service_case_number=(
            order.service_case.case_number if order.service_case_id else None
        ),
    )


# --- Lesende Endpoints (Dev-Phase ohne Auth) -------------------------------

@router.get("/work_orders", response=WorkOrderListOut)
def list_work_orders(
    request,
    filters: WorkOrderFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Aufträge auflisten: Suche (Titel/Nummer), Status-/Projekt-/Objekt-/Vorgangsfilter."""
    require(request, "workflow", "LESEN")
    qs = WorkOrder.objects.select_related(
        "property__address", "project", "service_case"
    )
    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(Q(title__icontains=needle) | Q(order_number__icontains=needle))
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.project_id:
        qs = qs.filter(project_id=filters.project_id)
    if filters.property_id:
        qs = qs.filter(property_id=filters.property_id)
    if filters.service_case_id:
        qs = qs.filter(service_case_id=filters.service_case_id)
    qs = qs.order_by("-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_work_order_out(o) for o in qs[start:start + page_size]]
    return WorkOrderListOut(items=items, total=total, page=page, page_size=page_size)


def _work_order_detail(work_order_id):
    order = (
        WorkOrder.objects.filter(id=work_order_id)
        .select_related("property__address", "project", "service_case")
        .prefetch_related("parties__party")
        .first()
    )
    if order is None:
        raise HttpError(404, "Auftrag nicht gefunden.")

    parties = [
        WorkOrderPartyOut(
            party_id=wp.party.id,
            display_name=wp.party.display_name,
            role=wp.role,
            is_primary=wp.is_primary,
            allocation_percent=wp.allocation_percent,
            source=wp.source,
        )
        for wp in sorted(
            order.parties.all(), key=lambda wp: (wp.role, not wp.is_primary)
        )
    ]
    changes = (
        StatusChange.objects.filter(entity="work_order", entity_id=order.id)
        .select_related("changed_by")
        .order_by("-occurred_at")
    )
    history = [
        StatusChangeOut(
            from_status=c.from_status,
            to_status=c.to_status,
            reason=c.reason,
            changed_by=c.changed_by.display_name if c.changed_by_id else None,
            occurred_at=c.occurred_at,
        )
        for c in changes
    ]

    base = _work_order_out(order)
    return WorkOrderDetailOut(
        **base.dict(),
        description=order.description,
        customer_reference=order.customer_reference,
        order_evidence_reference=order.order_evidence_reference,
        responsibility_confirmed_at=order.responsibility_confirmed_at,
        billing_mode=order.billing_mode,
        version=order.version,
        parties=parties,
        history=history,
    )


@router.get("/work_orders/{work_order_id}", response=WorkOrderDetailOut)
def get_work_order(request, work_order_id: UUID):
    """Detail eines Auftrags inkl. Beteiligter und Statusverlauf."""
    require(request, "workflow", "LESEN")
    return _work_order_detail(work_order_id)


class KundenhistorieOut(Schema):
    customer_party_id: UUID | None = None
    customer_name: str | None = None
    auftraege_gesamt: int
    termine_gesamt: int


@router.get("/work_orders/{work_order_id}/kundenhistorie", response=KundenhistorieOut)
def work_order_kundenhistorie(request, work_order_id: UUID):
    """Auftraggeber des Auftrags + wie viele Aufträge/Termine dieser Kunde
    insgesamt hat (rein lesend, workflow/LESEN)."""
    require(request, "workflow", "LESEN")
    if not WorkOrder.objects.filter(id=work_order_id).exists():
        raise HttpError(404, "Auftrag nicht gefunden.")
    return KundenhistorieOut(**auftrag_service.kundenhistorie(work_order_id))


# --- Schreibende Endpoints (Session-Auth Pflicht) --------------------------

@router.post("/work_orders", response={201: WorkOrderDetailOut}, auth=django_auth)
def create_work_order(request, payload: WorkOrderIn):
    """Neuen Auftrag (Status ENTWURF) anlegen."""
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        order = auftrag_service.create_work_order(
            actor,
            property_id=payload.property_id,
            title=payload.title,
            project_id=payload.project_id,
            service_case_id=payload.service_case_id,
            description=payload.description,
            priority=payload.priority,
            desired_date=payload.desired_date,
            customer_reference=payload.customer_reference,
            is_emergency=payload.is_emergency,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _work_order_detail(order.id))


@router.post(
    "/work_orders/{work_order_id}/parties",
    response={201: WorkOrderDetailOut},
    auth=django_auth,
)
def add_work_order_party(request, work_order_id: UUID, payload: WorkOrderPartyIn):
    """Beteiligten (Rolle) am Auftrag hinzufügen."""
    # Beteiligtenzuweisung ist eine Änderung am bestehenden Auftrag → AENDERN.
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        auftrag_service.add_work_order_party(
            actor,
            work_order_id=work_order_id,
            party_id=payload.party_id,
            role=payload.role,
            is_primary=payload.is_primary,
            allocation_percent=payload.allocation_percent,
            source=payload.source,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _work_order_detail(work_order_id))


@router.post(
    "/work_orders/{work_order_id}/responsibility",
    response=WorkOrderDetailOut,
    auth=django_auth,
)
def confirm_responsibility(request, work_order_id: UUID, payload: ResponsibilityIn):
    """Verantwortungsbereich bestätigen (A-21)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        auftrag_service.confirm_responsibility(
            actor, work_order_id=work_order_id, scope=payload.scope
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _work_order_detail(work_order_id)


@router.post(
    "/work_orders/{work_order_id}/evidence",
    response=WorkOrderDetailOut,
    auth=django_auth,
)
def set_order_evidence(request, work_order_id: UUID, payload: EvidenceIn):
    """Beauftragungsnachweis in Textform hinterlegen (A-26)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        auftrag_service.set_order_evidence(
            actor, work_order_id=work_order_id, reference=payload.reference
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _work_order_detail(work_order_id)


class WorkOrderPatchIn(Schema):
    """Änderbare Kopffelder des Auftrags. Zurzeit nur die Abrechnungsart."""
    billing_mode: str | None = None


@router.patch("/work_orders/{work_order_id}", response=WorkOrderDetailOut, auth=django_auth)
def update_work_order(request, work_order_id: UUID, payload: WorkOrderPatchIn):
    """Auftrag ändern — derzeit die **Abrechnungsart** (PAUSCHAL | REGIE).

    PAUSCHAL (Default): Die Rechnung ist die Angebotskopie; Zeiten und
    Berichtspositionen sind Nachweis, kein Rechnungsposten. REGIE: Die Rechnung
    entsteht aus Bericht + Zeiten.

    **Zwei Rechte, fail-closed** (Review-Befund H-2): Der Auftrag gehört dem
    Modul `workflow`, aber die Abrechnungsart entscheidet darüber, **wie und
    woraus abgerechnet wird** — sie ist eine kaufmännische Weichenstellung.
    Deshalb verlangt dieser Endpunkt zusätzlich `invoicing/AENDERN`. Wer Aufträge
    disponieren darf, darf damit nicht automatisch die Abrechnungsart umlegen.
    """
    actor, _ = require(request, "workflow", "AENDERN")
    require(request, "invoicing", "AENDERN")
    if payload.billing_mode is None:
        raise HttpError(422, "Es wurde kein änderbares Feld übergeben.")
    try:
        abrechnung_service.set_billing_mode(
            actor, work_order_id=work_order_id, billing_mode=payload.billing_mode
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _work_order_detail(work_order_id)


# --- Offene Abrechnung ------------------------------------------------------

class PreisVorschlagOut(Schema):
    art: str
    betrag: Decimal
    quelle: str


class OffeneBerichtspositionOut(Schema):
    site_report_line_id: UUID
    site_report_id: UUID
    report_date: date
    position_number: int
    line_type: str
    description: str
    quantity: Decimal | None = None
    unit: str | None = None
    # Der fehlende Preis wird HIER schon sichtbar — nicht erst beim
    # Abrechnungslauf. `einzelpreis` ist **null = unbekannt, nie 0**.
    preis_status: str            # BEKANNT | UNBEKANNT
    einzelpreis: Decimal | None = None
    grund: str | None = None
    grund_text: str | None = None
    vorschlaege: list[PreisVorschlagOut] = []


class OffeneZeitgruppeOut(Schema):
    """Abgerechnet wird je **Lohngruppe** — oder, ohne Lohngruppe, je Mitarbeiter
    (dann ist `quelle_id` seine app_user_id und `wage_group_id` null)."""
    quelle_id: UUID
    bezeichnung: str
    wage_group_id: UUID | None = None
    stunden: Decimal
    time_entry_ids: list[UUID]
    preis_status: str
    einzelpreis: Decimal | None = None
    grund: str | None = None
    grund_text: str | None = None
    vorschlaege: list[PreisVorschlagOut] = []


class UnsignierterBerichtOut(Schema):
    id: UUID
    report_date: date
    status: str
    activity_text: str


class OffeneAbrechnungOut(Schema):
    work_order_id: UUID
    billing_mode: str
    # False bei PAUSCHAL: Die Positionen sind dann **Nachweis**, kein
    # Rechnungsposten — das Angebot enthält die Leistung bereits.
    abrechenbar: bool
    hinweis: str
    berichtspositionen: list[OffeneBerichtspositionOut]
    zeitgruppen: list[OffeneZeitgruppeOut]
    nicht_unterzeichnete_berichte: list[UnsignierterBerichtOut]


@router.get("/work_orders/{work_order_id}/offene-abrechnung", response=OffeneAbrechnungOut)
def offene_abrechnung(request, work_order_id: UUID):
    """Was ist an diesem Auftrag noch **nicht** abgerechnet?

    Berichtspositionen und Zeitbuchungen ohne aktive Abrechnungsbindung — mit
    `preis_status`, damit ein fehlender Preis geklärt werden kann, **bevor**
    jemand fakturieren will.

    Das ist eine Auftragssicht über die ganze Baustelle; sie lässt sich nicht auf
    eigene Zeilen begrenzen. `require` ist fail-closed: Scope EIGENE → **403**.

    **Zwei Rechte, nicht eines: Geld hängt an `invoicing`.** Die Antwort führt
    Einzelpreise und Preisvorschläge — mit `workflow/LESEN` allein läse die
    Disposition die Preise der ganzen Baustelle mit. Das Entitäts-Dossier zieht
    dieselbe Grenze; die beiden dürfen nicht auseinanderlaufen. Wer abrechnen
    darf, hat `invoicing` ohnehin.
    """
    require(request, "workflow", "LESEN")
    require(request, "invoicing", "LESEN")
    try:
        return abrechnung_service.offene_abrechnung(work_order_id)
    except ValueError as exc:
        raise HttpError(404, str(exc))


@router.post(
    "/work_orders/{work_order_id}/status",
    response=WorkOrderDetailOut,
    auth=django_auth,
)
def advance_work_order_status(request, work_order_id: UUID, payload: StatusIn):
    """Statuswechsel des Auftrags durchführen (validiert gegen die Übergangstabelle;
    die Freigabe-/Abrechnungs-Tore prüft die DB)."""
    # Zweifelsfall: dieser eine Endpunkt bedient ALLE Statuswechsel. Der Übergang
    # in FREIGEGEBEN ist die eigentliche Auftrags-Freigabe (ein Freigabetor) und
    # verlangt daher das Recht FREIGEBEN; alle übrigen Wechsel sind AENDERN. So
    # kann ein Konto mit AENDERN, aber ohne FREIGEBEN, den Auftrag nicht freigeben.
    action = "FREIGEBEN" if payload.to_status == "FREIGEGEBEN" else "AENDERN"
    actor, _ = require(request, "workflow", action)
    try:
        auftrag_service.advance_status(
            actor,
            work_order_id=work_order_id,
            to_status=payload.to_status,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _work_order_detail(work_order_id)
