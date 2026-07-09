"""Projekt-API — Projekte (workflow.project) inkl. verknüpfter Liegenschaften
und Vorgänge (service_case).

Wie die übrigen APIs: Lesen in der Dev-Phase ohne Auth, Schreiben verlangt
Django-Session + zugeordnetes app_user. Views bleiben dünn, rufen die
Service-Schicht; Model-Instanzen verlassen die API nicht.
"""
from datetime import date, datetime
from uuid import UUID

from django.db.models import Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_create
from db_core.models import Checklist, Project, ProjectLog, ServiceCase, StatusChange
from db_core.services import projekt as projekt_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class CategoryOut(Schema):
    id: UUID
    name: str
    color_hex: str | None = None


class ProjectOut(Schema):
    id: UUID
    project_number: str
    name: str
    status: str
    start_date: date | None = None
    target_end_date: date | None = None
    category: CategoryOut | None = None


class ProjectListOut(Schema):
    items: list[ProjectOut]
    total: int
    page: int
    page_size: int


class PropertyRefOut(Schema):
    id: UUID
    property_number: str
    name: str
    city: str


class ServiceCaseOut(Schema):
    id: UUID
    case_number: str
    subject: str
    status: str
    priority: str
    received_at: datetime


class ProjectDetailOut(ProjectOut):
    version: int
    created_at: datetime
    updated_at: datetime
    properties: list[PropertyRefOut]
    service_cases: list[ServiceCaseOut]


class ProjectIn(Schema):
    name: str
    category_id: UUID | None = None
    property_ids: list[UUID] = []
    start_date: date | None = None
    target_end_date: date | None = None


class ProjectFilter(Schema):
    q: str | None = None
    status: str | None = None
    category_id: UUID | None = None


# --- Lesende Endpoints (Dev-Phase ohne Auth) -------------------------------

@router.get("/projects", response=ProjectListOut)
def list_projects(
    request,
    filters: ProjectFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Projekte auflisten: Suche (Name/Nummer), Status-/Kategoriefilter, Seiten."""
    require(request, "workflow", "LESEN")
    qs = Project.objects.select_related("category")

    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(Q(name__icontains=needle) | Q(project_number__icontains=needle))
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.category_id:
        qs = qs.filter(category_id=filters.category_id)

    qs = qs.order_by("-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_project_out(p) for p in qs[start:start + page_size]]
    return ProjectListOut(items=items, total=total, page=page, page_size=page_size)


def _category_out(project):
    cat = project.category
    if cat is None:
        return None
    return CategoryOut(id=cat.id, name=cat.name, color_hex=cat.color_hex)


def _project_out(project):
    return ProjectOut(
        id=project.id,
        project_number=project.project_number,
        name=project.name,
        status=project.status,
        start_date=project.start_date,
        target_end_date=project.target_end_date,
        category=_category_out(project),
    )


def _project_detail(project_id):
    project = (
        Project.objects.filter(id=project_id)
        .select_related("category")
        .prefetch_related(
            "property_links__property__address",
            "service_cases",
        )
        .first()
    )
    if project is None:
        raise HttpError(404, "Projekt nicht gefunden.")

    properties = [
        PropertyRefOut(
            id=link.property.id,
            property_number=link.property.property_number,
            name=link.property.name,
            city=link.property.address.city,
        )
        for link in sorted(
            project.property_links.all(), key=lambda l: l.property.property_number
        )
    ]
    service_cases = [
        ServiceCaseOut(
            id=c.id,
            case_number=c.case_number,
            subject=c.subject,
            status=c.status,
            priority=c.priority,
            received_at=c.received_at,
        )
        for c in sorted(project.service_cases.all(), key=lambda c: c.received_at, reverse=True)
    ]

    return ProjectDetailOut(
        id=project.id,
        project_number=project.project_number,
        name=project.name,
        status=project.status,
        start_date=project.start_date,
        target_end_date=project.target_end_date,
        category=_category_out(project),
        version=project.version,
        created_at=project.created_at,
        updated_at=project.updated_at,
        properties=properties,
        service_cases=service_cases,
    )


# --- Schreibender Endpoint (Session-Auth Pflicht) --------------------------

@router.post("/projects", response={201: ProjectDetailOut}, auth=django_auth)
def create_project(request, payload: ProjectIn):
    """Neues Projekt anlegen (workflow.project + optionale Liegenschafts-Links)."""
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        project = projekt_service.create_project(
            actor,
            name=payload.name,
            category_id=payload.category_id,
            property_ids=payload.property_ids,
            start_date=payload.start_date,
            target_end_date=payload.target_end_date,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _project_detail(project.id))


@router.get("/projects/{project_id}", response=ProjectDetailOut)
def get_project(request, project_id: UUID):
    """Detail eines Projekts inkl. Liegenschaften und Vorgängen."""
    require(request, "workflow", "LESEN")
    return _project_detail(project_id)


# --- Vorgang (service_case) Detail -----------------------------------------

class PartyRefOut(Schema):
    id: UUID
    display_name: str


class ProjectRefOut(Schema):
    id: UUID
    project_number: str
    name: str


class StatusChangeOut(Schema):
    from_status: str | None = None
    to_status: str
    reason: str | None = None
    changed_by: str | None = None
    occurred_at: datetime


class ServiceCaseDetailOut(Schema):
    id: UUID
    case_number: str
    subject: str
    description: str | None = None
    status: str
    priority: str
    responsibility_scope: str
    received_at: datetime
    property: PropertyRefOut
    project: ProjectRefOut | None = None
    reported_by: PartyRefOut | None = None
    history: list[StatusChangeOut]


# --- Projekt-Cockpit: Logbuch & Checklisten --------------------------------

class LogEntryOut(Schema):
    category: str
    entry: str
    created_by: str | None = None
    created_at: datetime


class ChecklistItemOut(Schema):
    position: int
    label: str
    done: bool
    done_by: str | None = None
    done_at: datetime | None = None


class ChecklistOut(Schema):
    id: UUID
    name: str
    items: list[ChecklistItemOut]


@router.get("/projects/{project_id}/log", response=list[LogEntryOut])
def get_project_log(request, project_id: UUID):
    """Logbuch-Einträge eines Projekts (neueste zuerst)."""
    require(request, "workflow", "LESEN")
    entries = (
        ProjectLog.objects.filter(project_id=project_id)
        .select_related("created_by")
        .order_by("-created_at")
    )
    return [
        LogEntryOut(
            category=e.category,
            entry=e.entry,
            created_by=e.created_by.display_name if e.created_by_id else None,
            created_at=e.created_at,
        )
        for e in entries
    ]


@router.get("/projects/{project_id}/checklists", response=list[ChecklistOut])
def get_project_checklists(request, project_id: UUID):
    """Checklisten eines Projekts inkl. Punkten (erledigt-Status)."""
    require(request, "workflow", "LESEN")
    checklists = (
        Checklist.objects.filter(project_id=project_id)
        .prefetch_related("items__done_by")
        .order_by("created_at")
    )
    result = []
    for cl in checklists:
        items = [
            ChecklistItemOut(
                position=i.position,
                label=i.label,
                done=i.done_at is not None,
                done_by=i.done_by.display_name if i.done_by_id else None,
                done_at=i.done_at,
            )
            for i in sorted(cl.items.all(), key=lambda i: i.position)
        ]
        result.append(ChecklistOut(id=cl.id, name=cl.name, items=items))
    return result


@router.get("/service_cases/{case_id}", response=ServiceCaseDetailOut)
def get_service_case(request, case_id: UUID):
    """Detail eines Vorgangs inkl. Liegenschaft, Projekt, Melder und
    Statusverlauf (append-only aus workflow.status_change)."""
    require(request, "workflow", "LESEN")
    case = (
        ServiceCase.objects.filter(id=case_id)
        .select_related("property__address", "project", "reported_by_party")
        .first()
    )
    if case is None:
        raise HttpError(404, "Vorgang nicht gefunden.")

    changes = (
        StatusChange.objects.filter(entity="service_case", entity_id=case.id)
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
    project = (
        ProjectRefOut(
            id=case.project.id,
            project_number=case.project.project_number,
            name=case.project.name,
        )
        if case.project_id
        else None
    )
    reporter = (
        PartyRefOut(
            id=case.reported_by_party.id,
            display_name=case.reported_by_party.display_name,
        )
        if case.reported_by_party_id
        else None
    )
    return ServiceCaseDetailOut(
        id=case.id,
        case_number=case.case_number,
        subject=case.subject,
        description=case.description,
        status=case.status,
        priority=case.priority,
        responsibility_scope=case.responsibility_scope,
        received_at=case.received_at,
        property=PropertyRefOut(
            id=case.property.id,
            property_number=case.property.property_number,
            name=case.property.name,
            city=case.property.address.city,
        ),
        project=project,
        reported_by=reporter,
        history=history,
    )


# --- Schreibende Cockpit-Endpoints (Session-Auth Pflicht) ------------------

class LogEntryIn(Schema):
    entry: str
    category: str = "NOTIZ"


class ChecklistIn(Schema):
    name: str
    items: list[str] = []


class ServiceCaseIn(Schema):
    property_id: UUID
    subject: str
    description: str | None = None
    reported_by_party_id: UUID | None = None
    priority: str = "NORMAL"


def _require_project(project_id):
    """Existenz des übergeordneten Projekts prüfen → 404 statt DB-FK-500."""
    if not Project.objects.filter(id=project_id).exists():
        raise HttpError(404, "Projekt nicht gefunden.")


@router.post(
    "/projects/{project_id}/log", response={201: LogEntryOut}, auth=django_auth
)
def add_project_log(request, project_id: UUID, payload: LogEntryIn):
    """Logbuch-Eintrag an einem Projekt anlegen (append-only).

    Torfunktion `require` (AENDERN): der Endpunkt wertet den row_scope NICHT aus.
    ProjectLog trägt kein setzbares Owner-Feld — `created_by` wird im Service
    zwingend auf den Akteur gesetzt, nicht aus dem Payload übernommen. Ein Konto
    mit Scope 'EIGENE' (Monteur) bekommt hier fail-closed 403; das Logbuch ist
    kein zeilenbegrenzter Bereich.
    """
    actor, _ = require(request, "workflow", "AENDERN")
    _require_project(project_id)
    try:
        log = projekt_service.add_project_log(
            actor,
            project_id=project_id,
            entry=payload.entry,
            category=payload.category,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    log = ProjectLog.objects.select_related("created_by").get(id=log.id)
    return Status(
        201,
        LogEntryOut(
            category=log.category,
            entry=log.entry,
            created_by=log.created_by.display_name if log.created_by_id else None,
            created_at=log.created_at,
        ),
    )


@router.post(
    "/projects/{project_id}/checklists", response={201: ChecklistOut}, auth=django_auth
)
def create_checklist(request, project_id: UUID, payload: ChecklistIn):
    """Checkliste (mit optionalen Punkten) an einem Projekt anlegen.

    Torfunktion `require_create` (ANLEGEN): die erzeugte Checkliste trägt kein
    setzbares Owner-Feld (`created_by` wird im Service auf den Akteur gesetzt),
    daher ist 'EIGENE' bedeutungslos — der Erzeuger ist per Definition der Akteur.
    """
    actor = require_create(request, "workflow", "ANLEGEN")
    _require_project(project_id)
    try:
        checklist = projekt_service.create_checklist(
            actor,
            project_id=project_id,
            name=payload.name,
            items=payload.items,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    checklist = Checklist.objects.prefetch_related("items").get(id=checklist.id)
    items = [
        ChecklistItemOut(
            position=i.position,
            label=i.label,
            done=i.done_at is not None,
            done_by=None,
            done_at=i.done_at,
        )
        for i in sorted(checklist.items.all(), key=lambda i: i.position)
    ]
    return Status(201, ChecklistOut(id=checklist.id, name=checklist.name, items=items))


@router.post(
    "/projects/{project_id}/service_cases",
    response={201: ServiceCaseOut},
    auth=django_auth,
)
def create_service_case(request, project_id: UUID, payload: ServiceCaseIn):
    """Vorgang (service_case) unter einem Projekt anlegen (Initialstatus NEU).

    Der Vorgang hängt fachlich an einer Liegenschaft (`property_id` Pflicht) und
    wird hier zusätzlich dem Projekt aus dem Pfad zugeordnet (`project_id`).

    Torfunktion `require_create` (ANLEGEN): der service_case trägt kein setzbares
    app_user-Owner-Feld (nur `reported_by_party_id`, ein Melder aus dem Kontakt-
    stamm — keine Zeilen-Zuordnung an einen Bearbeiter). 'EIGENE' ist damit
    bedeutungslos.
    """
    actor = require_create(request, "workflow", "ANLEGEN")
    _require_project(project_id)
    try:
        case = projekt_service.create_service_case(
            actor,
            property_id=payload.property_id,
            subject=payload.subject,
            project_id=project_id,
            description=payload.description,
            reported_by_party_id=payload.reported_by_party_id,
            priority=payload.priority,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(
        201,
        ServiceCaseOut(
            id=case.id,
            case_number=case.case_number,
            subject=case.subject,
            status=case.status,
            priority=case.priority,
            received_at=case.received_at,
        ),
    )
