"""Planungs-API — Einsätze/Termine (workflow.service_job) inkl. Zuweisungen,
Statusverlauf sowie erfasster Zeiten und Materialien.

Read-only in der Dev-Phase (kein Anlegen/Umplanen ohne Auth — bewusste
Entscheidung; die Schreib-Endpunkte kommen mit dem Auth-Slice). Wie die übrigen
APIs bleiben die Views dünn; Model-Instanzen verlassen die API nicht.

Der Einsatz trägt keinen eigenen Titel — er kommt vom zugehörigen Auftrag
(work_order); dessen Liegenschaft liefert den Ort. Zugewiesene sind interne
security.app_user, der Vor-Ort-Ansprechpartner ist eine identity.party.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.db.models import Count, F, Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from db_core.models import (
    JobAssignment,
    MaterialEntry,
    ServiceJob,
    StatusChange,
    TimeEntry,
)

router = Router()

# Einsatzstatus (workflow.service_job, Migration 0014) — für Filter-Validierung.
JOB_STATUSES = (
    "UNGEPLANT",
    "GEPLANT",
    "BESTAETIGT",
    "UNTERWEGS",
    "VOR_ORT",
    "PAUSIERT",
    "ABGESCHLOSSEN",
    "NACHARBEIT",
    "AUSGEFALLEN",
)


# --- Schemas ---------------------------------------------------------------

class WorkOrderRefOut(Schema):
    id: UUID
    order_number: str
    title: str
    status: str


class PropertyRefOut(Schema):
    id: UUID
    property_number: str
    name: str
    city: str


class ServiceJobOut(Schema):
    id: UUID
    job_number: str
    status: str
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    work_order: WorkOrderRefOut
    property: PropertyRefOut | None = None
    assignee_count: int = 0


class ServiceJobListOut(Schema):
    items: list[ServiceJobOut]
    total: int
    page: int
    page_size: int


class AssignmentOut(Schema):
    assignee_id: UUID
    display_name: str
    role: str


class StatusChangeOut(Schema):
    from_status: str | None = None
    to_status: str
    reason: str | None = None
    changed_by: str | None = None
    occurred_at: datetime


class TimeEntryOut(Schema):
    time_type: str
    started_at: datetime
    ended_at: datetime
    note: str | None = None
    user: str | None = None


class MaterialEntryOut(Schema):
    description: str
    quantity: Decimal
    unit: str
    note: str | None = None


class ServiceJobDetailOut(ServiceJobOut):
    access_instructions: str | None = None
    completion_notes: str | None = None
    on_site_contact: str | None = None
    created_at: datetime
    assignments: list[AssignmentOut]
    history: list[StatusChangeOut]
    time_entries: list[TimeEntryOut]
    material_entries: list[MaterialEntryOut]


class ServiceJobFilter(Schema):
    q: str | None = None
    status: str | None = None
    work_order_id: UUID | None = None
    scheduled_from: datetime | None = None
    scheduled_to: datetime | None = None


# --- Mapper ----------------------------------------------------------------

def _property_ref(job):
    order = job.work_order
    p = getattr(order, "property", None)
    if p is None:
        return None
    return PropertyRefOut(
        id=p.id, property_number=p.property_number, name=p.name, city=p.address.city
    )


def _work_order_ref(job):
    order = job.work_order
    return WorkOrderRefOut(
        id=order.id,
        order_number=order.order_number,
        title=order.title,
        status=order.status,
    )


def _service_job_out(job, assignee_count=0):
    return ServiceJobOut(
        id=job.id,
        job_number=job.job_number,
        status=job.status,
        scheduled_start=job.scheduled_start,
        scheduled_end=job.scheduled_end,
        actual_start=job.actual_start,
        actual_end=job.actual_end,
        work_order=_work_order_ref(job),
        property=_property_ref(job),
        assignee_count=assignee_count,
    )


# --- Lesende Endpoints (Dev-Phase ohne Auth) -------------------------------

@router.get("/einsaetze", response=ServiceJobListOut)
def list_einsaetze(
    request,
    filters: ServiceJobFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Einsätze auflisten: Suche (Einsatz-/Auftragsnummer, Auftragstitel),
    Status-/Auftrags-/Zeitraumfilter. Sortiert nach Planbeginn (geplante zuerst,
    ungeplante ans Ende), dann Anlegedatum."""
    if filters.status and filters.status not in JOB_STATUSES:
        raise HttpError(422, f"Unbekannter Status '{filters.status}'.")

    qs = ServiceJob.objects.select_related(
        "work_order__property__address"
    )
    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(
            Q(job_number__icontains=needle)
            | Q(work_order__order_number__icontains=needle)
            | Q(work_order__title__icontains=needle)
        )
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.work_order_id:
        qs = qs.filter(work_order_id=filters.work_order_id)
    if filters.scheduled_from:
        qs = qs.filter(scheduled_start__gte=filters.scheduled_from)
    if filters.scheduled_to:
        qs = qs.filter(scheduled_start__lte=filters.scheduled_to)
    # NULLs (ungeplant) ans Ende, sonst aufsteigend nach Planbeginn.
    qs = qs.order_by(F("scheduled_start").asc(nulls_last=True), "-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    window = list(qs[start:start + page_size])
    counts = _assignee_counts([j.id for j in window])
    items = [_service_job_out(j, counts.get(j.id, 0)) for j in window]
    return ServiceJobListOut(items=items, total=total, page=page, page_size=page_size)


def _assignee_counts(job_ids):
    if not job_ids:
        return {}
    rows = (
        JobAssignment.objects.filter(service_job_id__in=job_ids)
        .values_list("service_job_id")
        .annotate(n=Count("id"))
    )
    return {sid: n for sid, n in rows}


@router.get("/einsaetze/{job_id}", response=ServiceJobDetailOut)
def get_einsatz(request, job_id: UUID):
    """Detail eines Einsatzes inkl. Zuweisungen, Statusverlauf, erfasster Zeiten
    und Materialien."""
    job = (
        ServiceJob.objects.filter(id=job_id)
        .select_related("work_order__property__address", "on_site_contact_party")
        .prefetch_related("assignments__assignee")
        .first()
    )
    if job is None:
        raise HttpError(404, "Einsatz nicht gefunden.")

    assignments = [
        AssignmentOut(
            assignee_id=a.assignee.id,
            display_name=a.assignee.display_name,
            role=a.role,
        )
        for a in sorted(
            job.assignments.all(), key=lambda a: (a.role != "LEAD", a.assignee.display_name)
        )
    ]
    changes = (
        StatusChange.objects.filter(entity="service_job", entity_id=job.id)
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
    times = (
        TimeEntry.objects.filter(service_job_id=job.id)
        .select_related("user")
        .order_by("started_at")
    )
    time_entries = [
        TimeEntryOut(
            time_type=t.time_type,
            started_at=t.started_at,
            ended_at=t.ended_at,
            note=t.note,
            user=t.user.display_name if t.user_id else None,
        )
        for t in times
    ]
    materials = MaterialEntry.objects.filter(service_job_id=job.id).order_by("created_at")
    material_entries = [
        MaterialEntryOut(
            description=m.description, quantity=m.quantity, unit=m.unit, note=m.note
        )
        for m in materials
    ]

    base = _service_job_out(job, len(assignments))
    contact = (
        job.on_site_contact_party.display_name if job.on_site_contact_party_id else None
    )
    return ServiceJobDetailOut(
        **base.dict(),
        access_instructions=job.access_instructions,
        completion_notes=job.completion_notes,
        on_site_contact=contact,
        created_at=job.created_at,
        assignments=assignments,
        history=history,
        time_entries=time_entries,
        material_entries=material_entries,
    )


# --- Plantafel-Board (Schwimmbahnen) ---------------------------------------

class BoardResourceOut(Schema):
    id: UUID
    display_name: str


class BoardJobOut(Schema):
    id: UUID
    job_number: str
    title: str
    status: str
    scheduled_start: datetime
    scheduled_end: datetime | None = None
    property_name: str | None = None
    assignee_ids: list[UUID]


class PlantafelOut(Schema):
    date_from: date
    date_to: date
    resources: list[BoardResourceOut]
    jobs: list[BoardJobOut]
    unassigned_count: int


@router.get("/plantafel", response=PlantafelOut)
def plantafel(
    request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    """Plantafel-Daten für einen Zeitraum: die Mitarbeiter-Bahnen (aus den
    Zuweisungen der Einsätze im Fenster) und die verplanten Einsätze mit ihren
    assignee_ids. Nur Einsätze mit Planbeginn erscheinen; Mehrfachzuweisungen
    tauchen in jeder betroffenen Bahn auf (n:m). Standardfenster: 7 Tage ab heute,
    maximal 31 Tage."""
    today = date.today()
    start = date_from or today
    end = date_to or (start + timedelta(days=6))
    if end < start:
        raise HttpError(422, "date_to darf nicht vor date_from liegen.")
    if (end - start).days > 45:
        raise HttpError(422, "Der Zeitraum darf höchstens 45 Tage umfassen.")

    jobs = (
        ServiceJob.objects.filter(
            scheduled_start__date__gte=start, scheduled_start__date__lte=end
        )
        .select_related("work_order__property")
        .prefetch_related("assignments__assignee")
        .order_by("scheduled_start", "id")
    )

    resources: dict = {}
    unassigned = 0
    out_jobs = []
    for j in jobs:
        assignee_ids = []
        for a in j.assignments.all():
            resources[a.assignee_id] = a.assignee.display_name
            assignee_ids.append(a.assignee_id)
        if not assignee_ids:
            unassigned += 1
        out_jobs.append(
            BoardJobOut(
                id=j.id,
                job_number=j.job_number,
                title=j.work_order.title,
                status=j.status,
                scheduled_start=j.scheduled_start,
                scheduled_end=j.scheduled_end,
                property_name=j.work_order.property.name,
                assignee_ids=assignee_ids,
            )
        )
    resource_list = [
        BoardResourceOut(id=uid, display_name=name)
        for uid, name in sorted(resources.items(), key=lambda kv: kv[1])
    ]
    return PlantafelOut(
        date_from=start,
        date_to=end,
        resources=resource_list,
        jobs=out_jobs,
        unassigned_count=unassigned,
    )
