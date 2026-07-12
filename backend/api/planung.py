"""Planungs-API — Einsätze/Termine (workflow.service_job) inkl. Zuweisungen,
Statusverlauf sowie erfasster Zeiten und Materialien.

Read-only in der Dev-Phase (kein Anlegen/Umplanen ohne Auth — bewusste
Entscheidung; die Schreib-Endpunkte kommen mit dem Auth-Slice). Wie die übrigen
APIs bleiben die Views dünn; Model-Instanzen verlassen die API nicht.

Titel und Ort: Der auftragsgebundene Einsatz erbt beides vom Auftrag (work_order
→ property). Der **freie Termin** (Migration 0062, work_order_id IS NULL — eine
Begehung/Besichtigung/Beratung vor der Beauftragung) trägt einen eigenen Titel
(Pflicht) und optional eine eigene Liegenschaft. `_job_title`/`_job_property`
lösen beides auf; das UI muss nicht selbst mischen. Zugewiesene sind interne
security.app_user, der Vor-Ort-Ansprechpartner ist eine identity.party (bei
Begehungen oft erst nachträglich bekannt → PATCH /einsaetze/{id}).
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.db.models import Count, F, Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require, require_scoped
from db_core.models import (
    AppointmentCategory,
    AppUser,
    JobAssignment,
    JobResource,
    MaterialEntry,
    Resource,
    ServiceJob,
    StatusChange,
    TimeEntry,
)
from db_core.services import einsatz as einsatz_service
from db_core.services import planung as planung_service

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


class CategoryRefOut(Schema):
    """Kategorie am Einsatz — Name IMMER dabei (Kalender/Plantafel zeigen den
    Namen als Text, die Farbe ist nur Ergänzung; WCAG: Status nie nur Farbe)."""

    id: UUID
    name: str
    color_token: str


class ResourceRefOut(Schema):
    id: UUID
    resource_number: str
    name: str
    resource_type: str


class ServiceJobOut(Schema):
    id: UUID
    job_number: str
    status: str
    # Anzeigetitel — beim freien Termin der eigene Titel, sonst der Auftragstitel
    # (bzw. ein eigener Titel, falls gesetzt). Das UI muss nie selbst mischen.
    title: str
    # Freier Termin (Begehung/Besichtigung/Beratung ohne Auftrag): work_order ist
    # dann None. `is_free` ist redundant, aber ausdrücklich — das UI kennzeichnet
    # den freien Termin als TEXT (WCAG: nie nur über Farbe).
    is_free: bool = False
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    work_order: WorkOrderRefOut | None = None
    property: PropertyRefOut | None = None
    category: CategoryRefOut | None = None
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
    resources: list[ResourceRefOut] = []
    history: list[StatusChangeOut]
    time_entries: list[TimeEntryOut]
    material_entries: list[MaterialEntryOut]


class ServiceJobFilter(Schema):
    q: str | None = None
    status: str | None = None
    work_order_id: UUID | None = None
    scheduled_from: datetime | None = None
    scheduled_to: datetime | None = None


class ServiceJobCreateIn(Schema):
    # Ohne work_order_id entsteht ein FREIER TERMIN; dann ist `title` Pflicht
    # (der Service prüft das, 422). Mit work_order_id ist `title` optional
    # (Fallback: Auftragstitel) und `property_id` muss zum Auftrag passen.
    work_order_id: UUID | None = None
    title: str | None = None
    property_id: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    on_site_contact_party_id: UUID | None = None
    access_instructions: str | None = None
    appointment_category_id: UUID | None = None


class ServiceJobUpdateIn(Schema):
    """Teil-Update: nur mitgeschickte Felder werden geändert.

    Ein ausdrückliches ``null`` löscht das Feld (z. B. Kontakt entfernen) —
    deshalb müssen die Felder von „nicht mitgeschickt" unterscheidbar sein
    (Pydantic: ``model_fields_set``). Der Auftragsbezug fehlt hier bewusst: er ist
    in der DB unveränderlich (WF-01).
    """

    on_site_contact_party_id: UUID | None = None
    title: str | None = None
    property_id: UUID | None = None
    access_instructions: str | None = None


class ScheduleIn(Schema):
    scheduled_start: datetime
    scheduled_end: datetime | None = None


class StatusAdvanceIn(Schema):
    to_status: str
    reason: str | None = None


class AssignmentIn(Schema):
    assignee_user_id: UUID
    role: str = "TECHNICIAN"


class TimeLogIn(Schema):
    time_type: str
    started_at: datetime
    ended_at: datetime
    # Nur für Scope ALLE (Disposition/Leitung) auswertbar: Zeit für eine andere
    # Person buchen. Bei Scope EIGENE (Monteur) wird auf den Akteur gezwungen.
    user_id: UUID | None = None
    note: str | None = None


class MaterialLogIn(Schema):
    description: str
    quantity: Decimal
    unit: str
    note: str | None = None


# --- Mapper ----------------------------------------------------------------

def _job_property(job):
    """Liegenschaft des Einsatzes: die eigene, sonst die des Auftrags.

    Der freie Termin hat keinen Auftrag — seine Liegenschaft (falls gepflegt)
    steht direkt am Einsatz. Beide Wege sind vorab per select_related geladen
    (kein N+1); wenn sie gesetzt sind, sind sie laut DB-FK identisch.
    """
    if job.property_id is not None:
        return job.property
    order = job.work_order
    return getattr(order, "property", None) if order is not None else None


def _property_ref(job):
    p = _job_property(job)
    if p is None:
        return None
    return PropertyRefOut(
        id=p.id, property_number=p.property_number, name=p.name, city=p.address.city
    )


def _job_title(job):
    """Anzeigetitel: eigener Titel, sonst Auftragstitel. Beim freien Termin ist
    der eigene Titel per DB-CHECK immer vorhanden."""
    if job.title:
        return job.title
    order = job.work_order
    return order.title if order is not None else ""


def _work_order_ref(job):
    order = job.work_order
    if order is None:
        return None
    return WorkOrderRefOut(
        id=order.id,
        order_number=order.order_number,
        title=order.title,
        status=order.status,
    )


def _category_ref(job):
    c = getattr(job, "appointment_category", None)
    if c is None:
        return None
    return CategoryRefOut(id=c.id, name=c.name, color_token=c.color_token)


def _service_job_out(job, assignee_count=0):
    return ServiceJobOut(
        id=job.id,
        job_number=job.job_number,
        status=job.status,
        title=_job_title(job),
        is_free=job.work_order_id is None,
        scheduled_start=job.scheduled_start,
        scheduled_end=job.scheduled_end,
        actual_start=job.actual_start,
        actual_end=job.actual_end,
        work_order=_work_order_ref(job),
        property=_property_ref(job),
        category=_category_ref(job),
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
    """Einsätze auflisten: Suche (Einsatz-/Auftragsnummer, Titel des Einsatzes
    oder des Auftrags), Status-/Auftrags-/Zeitraumfilter. Sortiert nach Planbeginn
    (geplante zuerst, ungeplante ans Ende), dann Anlegedatum.

    Zeilenbegrenzung: Wer nur 'EIGENE' sehen darf (Monteur), bekommt
    ausschließlich Einsätze, denen er über workflow.job_assignment zugewiesen
    ist. Das gilt unverändert auch für **freie Termine** (ohne Auftrag): Die
    Sichtbarkeit hängt allein an der Zuweisung, nie an Auftrag/Liegenschaft — ein
    freier Termin ohne Zuweisung ist für einen 'EIGENE'-Nutzer damit gar nicht
    sichtbar (fail-closed), er wird durch den fehlenden Auftrag nicht plötzlich
    öffentlich."""
    actor, scope = require_scoped(request, "workflow", "LESEN")
    if filters.status and filters.status not in JOB_STATUSES:
        raise HttpError(422, f"Unbekannter Status '{filters.status}'.")

    qs = ServiceJob.objects.select_related(
        "work_order__property__address", "property__address", "appointment_category"
    )
    if scope == "EIGENE":
        qs = qs.filter(assignments__assignee_id=actor).distinct()
    if filters.q:
        needle = filters.q.strip()
        # work_order ist NULL-fähig → Django joint LEFT OUTER; freie Termine
        # fallen dadurch nicht aus der Suche heraus.
        qs = qs.filter(
            Q(job_number__icontains=needle)
            | Q(title__icontains=needle)
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


def _einsatz_detail(job_id):
    """Baut die Detailantwort eines Einsatzes — OHNE jede Rechteprüfung.

    Reiner Mapper. Die Torfunktionen laufen beim Aufrufer (get_einsatz prüft
    LESEN, update_einsatz hat AENDERN samt Scope-Guard schon geprüft). Deshalb
    darf der Schreibpfad NICHT get_einsatz aufrufen: das löste eine zweite,
    andere Prüfung (LESEN) aus und beantwortete einen bereits geschriebenen
    Vorgang mit 403/404 — der Nutzer sähe einen Fehler, obwohl gespeichert wurde.
    """
    job = (
        ServiceJob.objects.filter(id=job_id)
        .select_related(
            "work_order__property__address",
            "property__address",
            "on_site_contact_party",
            "appointment_category",
        )
        .prefetch_related("assignments__assignee", "resource_links__resource")
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

    resources = [
        ResourceRefOut(
            id=link.resource.id,
            resource_number=link.resource.resource_number,
            name=link.resource.name,
            resource_type=link.resource.resource_type,
        )
        for link in sorted(
            job.resource_links.all(), key=lambda link: link.resource.name
        )
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
        resources=resources,
        history=history,
        time_entries=time_entries,
        material_entries=material_entries,
    )


@router.get("/einsaetze/{job_id}", response=ServiceJobDetailOut)
def get_einsatz(request, job_id: UUID):
    """Detail eines Einsatzes inkl. Zuweisungen, Statusverlauf, erfasster Zeiten
    und Materialien.

    Zeilenbegrenzung: Bei Scope 'EIGENE' (Monteur) ist ein Einsatz, dem der
    Akteur nicht zugewiesen ist, mit 404 abgeriegelt — die Existenz fremder
    Einsätze wird nicht verraten."""
    actor, scope = require_scoped(request, "workflow", "LESEN")
    _load_job_or_404(job_id)
    _guard_own_job(job_id, actor, scope)
    return _einsatz_detail(job_id)


# --- Benutzer-Auswahlliste (Zuweisung) -------------------------------------

class AssignableUserOut(Schema):
    id: UUID
    display_name: str


@router.get("/users", response=list[AssignableUserOut])
def list_assignable_users(request, q: str | None = Query(None)):
    """Aktive Benutzer (security.app_user) als schlanke Zuweisungs-Auswahlliste.

    Speist die Zuweisung von Einsätzen (assignee_user_id) und Aufgaben sowie das
    Buchen von Zeit für eine andere Person. Deshalb liegt der Endpunkt beim
    Modul `workflow` (Aktion LESEN) — es ist eine Arbeits-Zuweisungsliste, KEIN
    Personalstammsatz (kein `hr`): ein Disponent ohne hr-Recht muss Monteure
    einplanen können, hat aber hier über sein `workflow`-LESEN Zugriff.

    Torfunktion `require` (fail-closed): Ein MONTEUR hat `workflow`/LESEN nur als
    Scope 'EIGENE' und bekommt bewusst 403 — das ist konsistent, denn er darf
    ohnehin nur sich selbst zuweisen (siehe log_time) und braucht keine
    Fremd-Auswahlliste.

    Datenminimierung: Ausgabe strikt auf id + display_name beschränkt — keine
    E-Mail, kein Status, keine sonstigen Personendaten.
    """
    require(request, "workflow", "LESEN")
    qs = AppUser.objects.filter(status="ACTIVE")
    if q:
        qs = qs.filter(display_name__icontains=q.strip())
    return [
        AssignableUserOut(id=u.id, display_name=u.display_name)
        for u in qs.order_by("display_name", "id")[:200]
    ]


# --- Schreibende Endpoints (Session-Auth Pflicht) --------------------------

def _reload_job(job_id):
    job = (
        ServiceJob.objects.filter(id=job_id)
        .select_related(
            "work_order__property__address",
            "property__address",
            "appointment_category",
        )
        .first()
    )
    if job is None:
        raise HttpError(404, "Einsatz nicht gefunden.")
    counts = _assignee_counts([job.id])
    return _service_job_out(job, counts.get(job.id, 0))


def _load_job_or_404(job_id):
    """Existenzprüfung: fehlender Einsatz → 404 (für ALLE wie EIGENE)."""
    if not ServiceJob.objects.filter(id=job_id).exists():
        raise HttpError(404, "Einsatz nicht gefunden.")


def _guard_own_job(job_id, actor, scope):
    """Bei Scope 'EIGENE' (Monteur): nur ein Einsatz, dem der Akteur über
    workflow.job_assignment zugewiesen ist, ist zugänglich. Fremder (oder nicht
    zugewiesener) Einsatz → 404, nicht 403 — die Existenz wird nicht verraten.
    Muster: get_einsatz."""
    if scope != "EIGENE":
        return
    if not JobAssignment.objects.filter(
        service_job_id=job_id, assignee_id=actor
    ).exists():
        raise HttpError(404, "Einsatz nicht gefunden.")


@router.post("/einsaetze", response={201: ServiceJobOut}, auth=django_auth)
def create_einsatz(request, payload: ServiceJobCreateIn):
    """Legt einen Einsatz (workflow.service_job) im Initialstatus UNGEPLANT an.

    Zwei Spielarten: **auftragsgebunden** (work_order_id gesetzt) oder **freier
    Termin** (work_order_id fehlt → Begehung/Besichtigung/Beratung; `title` ist
    dann Pflicht, Liegenschaft und Kontakt bleiben optional).

    `require` (fail-closed): der Einsatz trägt kein Owner-/Zuweisungsfeld im
    Payload (Zuweisungen laufen separat über /assignments). Einsätze legt die
    Disposition/Leitung an — ein Monteur mit 'EIGENE'-Scope bekommt 403. Das gilt
    für den freien Termin genauso: er wäre sonst eine Zeile, die der Monteur zwar
    anlegen, aber selbst nicht sehen könnte (die 'EIGENE'-Sicht hängt an der
    Zuweisung, die er nicht setzen darf).

    Die DB-Tore (Startstatus UNGEPLANT, keine Anlage auf abgerechnete/stornierte
    Aufträge B-03/B-06) kommen als 422 zurück."""
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        job = einsatz_service.create_service_job(
            actor,
            work_order_id=payload.work_order_id,
            title=payload.title,
            property_id=payload.property_id,
            scheduled_start=payload.scheduled_start,
            scheduled_end=payload.scheduled_end,
            on_site_contact_party_id=payload.on_site_contact_party_id,
            access_instructions=payload.access_instructions,
            appointment_category_id=payload.appointment_category_id,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _reload_job(job.id))


# Felder, die ein Nutzer mit Scope 'EIGENE' (Monteur) an einem FREIEN Termin
# nachtragen darf: was er vor Ort erfährt. Titel und Liegenschaft sind
# Dispositionsdaten und bleiben ihm verwehrt (403) — sonst könnte er einen fremd
# geplanten Termin umwidmen oder ihn einer beliebigen Liegenschaft zuordnen.
_EIGENE_UPDATE_FELDER = {"on_site_contact_party_id", "access_instructions"}


@router.patch("/einsaetze/{job_id}", response=ServiceJobDetailOut, auth=django_auth)
def update_einsatz(request, job_id: UUID, payload: ServiceJobUpdateIn):
    """Trägt Angaben am Einsatz nach — vor allem den **Ansprechpartner vor Ort**.

    Bei einer Begehung ist der Kontakt oft noch nicht angelegt; er wird nach dem
    Termin nachgetragen. Nur mitgeschickte Felder werden geändert; ein
    ausdrückliches ``null`` löscht das Feld (Kontakt entfernen).

    `require_scoped` (Muster wie Zeit-/Materialbuchung): Ein Monteur (Scope
    'EIGENE') MUSS auf seinem eigenen **freien Termin** den Kontakt und die
    Zutrittshinweise nachtragen können — genau dort entsteht der Kontakt ja erst
    vor Ort. Ein nicht zugewiesener Einsatz ist für ihn mit 404 abgeriegelt.

    Alles andere ist für ihn gesperrt (403):
    * Titel und Liegenschaft sind **immer** Dispositionsdaten,
    * an einem **auftragsgebundenen** Einsatz ist auch der Vor-Ort-Kontakt ein
      Dispositionsdatum (die Disposition hat ihn mit dem Auftrag gesetzt) — der
      Monteur soll ihn dort nicht durch eine beliebige Party ersetzen oder
      löschen können.

    Der Auftragsbezug ist gar nicht änderbar (DB-Trigger WF-01)."""
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    job = ServiceJob.objects.filter(id=job_id).only("id", "work_order_id").first()
    if job is None:
        raise HttpError(404, "Einsatz nicht gefunden.")
    _guard_own_job(job_id, actor, scope)
    gesetzt = payload.model_fields_set
    if scope == "EIGENE":
        if job.work_order_id is not None:
            raise HttpError(
                403,
                "Angaben eines auftragsgebundenen Einsatzes pflegt die "
                "Disposition.",
            )
        if gesetzt - _EIGENE_UPDATE_FELDER:
            raise HttpError(
                403,
                "Ihre Rolle erlaubt am Termin nur das Nachtragen von "
                "Ansprechpartner und Zutrittshinweisen.",
            )
    felder = {name: getattr(payload, name) for name in gesetzt}
    try:
        einsatz_service.update_service_job(actor, service_job_id=job_id, **felder)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    # Bewusst der reine Mapper, NICHT get_einsatz: der löste eine zweite Prüfung
    # (LESEN) aus und meldete nach erfolgreichem Schreiben 403/404.
    return _einsatz_detail(job_id)


@router.post("/einsaetze/{job_id}/schedule", response=ServiceJobOut, auth=django_auth)
def set_schedule(request, job_id: UUID, payload: ScheduleIn):
    """Setzt/ändert den Planungszeitraum eines Einsatzes (ohne Statuswechsel).

    `require` (fail-closed): Umplanen ist Dispositionssache; der Monteur (Scope
    'EIGENE') bekommt 403. Der DB-CHECK verlangt scheduled_end > scheduled_start
    (→ 422)."""
    actor, _ = require(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    try:
        einsatz_service.set_schedule(
            actor,
            service_job_id=job_id,
            scheduled_start=payload.scheduled_start,
            scheduled_end=payload.scheduled_end,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_job(job_id)


@router.post("/einsaetze/{job_id}/status", response=ServiceJobOut, auth=django_auth)
def advance_status(request, job_id: UUID, payload: StatusAdvanceIn):
    """Führt einen Statuswechsel des Einsatzes durch.

    `require` (fail-closed): den Status steuert die Disposition/Leitung; Monteur
    (Scope 'EIGENE') → 403. Unzulässige/begründungspflichtige Übergänge und die
    fachlichen Tore (Ausführung ab UNTERWEGS setzt einen freigegebenen Auftrag
    voraus) kommen als 422."""
    actor, _ = require(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    try:
        einsatz_service.advance_status(
            actor, service_job_id=job_id, to_status=payload.to_status,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_job(job_id)


@router.post(
    "/einsaetze/{job_id}/assignments", response={201: AssignmentOut}, auth=django_auth
)
def assign_user(request, job_id: UUID, payload: AssignmentIn):
    """Weist dem Einsatz einen Mitarbeiter zu.

    `require` (fail-closed): Wer wen einplant, entscheidet die Disposition/Leitung
    — ein Monteur darf sich (oder andere) nicht selbst zuweisen, sonst könnte er
    sich fremde Einsätze über die 'EIGENE'-Grenze holen; Scope 'EIGENE' → 403."""
    actor, _ = require(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    try:
        assignment = einsatz_service.assign_user(
            actor,
            service_job_id=job_id,
            assignee_user_id=payload.assignee_user_id,
            role=payload.role,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    assignment = JobAssignment.objects.select_related("assignee").get(id=assignment.id)
    return Status(
        201,
        AssignmentOut(
            assignee_id=assignment.assignee_id,
            display_name=assignment.assignee.display_name,
            role=assignment.role,
        ),
    )


@router.post("/einsaetze/{job_id}/times", response={201: TimeEntryOut}, auth=django_auth)
def log_time(request, job_id: UUID, payload: TimeLogIn):
    """Erfasst eine Zeit am Einsatz (B-27).

    `require_scoped`: ein Monteur MUSS auf seinen Einsätzen Zeiten buchen können.
    Bei Scope 'EIGENE' ist ein nicht zugewiesener Einsatz mit 404 abgeriegelt, und
    die user_id wird auf den Akteur gezwungen — fremde Zeiten zu buchen ist
    verboten (403 bei explizit fremder user_id). Das Korrekturfenster B-28 prüft
    die DB (→ 422)."""
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    _guard_own_job(job_id, actor, scope)
    user_id = payload.user_id
    if scope == "EIGENE":
        if user_id not in (None, actor):
            raise HttpError(
                403,
                "Ihre Rolle erlaubt nur eigene Zeiten; eine Zeit kann nicht für "
                "eine andere Person gebucht werden.",
            )
        user_id = actor
    elif user_id is None:
        user_id = actor
    try:
        entry = einsatz_service.log_time(
            actor,
            service_job_id=job_id,
            user_id=user_id,
            time_type=payload.time_type,
            started_at=payload.started_at,
            ended_at=payload.ended_at,
            note=payload.note,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    entry = TimeEntry.objects.select_related("user").get(id=entry.id)
    return Status(
        201,
        TimeEntryOut(
            time_type=entry.time_type,
            started_at=entry.started_at,
            ended_at=entry.ended_at,
            note=entry.note,
            user=entry.user.display_name if entry.user_id else None,
        ),
    )


@router.post(
    "/einsaetze/{job_id}/materials", response={201: MaterialEntryOut}, auth=django_auth
)
def log_material(request, job_id: UUID, payload: MaterialLogIn):
    """Erfasst einen Materialverbrauch am Einsatz (B-26: reine Verbrauchserfassung).

    `require_scoped`: der Monteur bucht Material auf seinen zugewiesenen Einsätzen
    (fremder Einsatz → 404). recorded_by ist stets der Akteur — es gibt kein
    fremdes Owner-Feld zu setzen. Das Korrekturfenster B-28 prüft die DB (→ 422)."""
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    _guard_own_job(job_id, actor, scope)
    try:
        entry = einsatz_service.log_material(
            actor,
            service_job_id=job_id,
            description=payload.description,
            quantity=payload.quantity,
            unit=payload.unit,
            recorded_by=actor,
            note=payload.note,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(
        201,
        MaterialEntryOut(
            description=entry.description,
            quantity=entry.quantity,
            unit=entry.unit,
            note=entry.note,
        ),
    )


# --- Plantafel-Board (Schwimmbahnen) ---------------------------------------

class BoardResourceOut(Schema):
    id: UUID
    display_name: str


class BoardResourceLaneOut(Schema):
    """Bahn einer Betriebsmittel-Ressource (Fahrzeug/Gerät/Raum)."""

    id: UUID
    display_name: str
    resource_type: str


class BoardJobOut(Schema):
    id: UUID
    job_number: str
    title: str
    status: str
    # Freier Termin ohne Auftrag: die Kachel kennzeichnet ihn als TEXT.
    is_free: bool = False
    scheduled_start: datetime
    scheduled_end: datetime | None = None
    property_name: str | None = None
    category: CategoryRefOut | None = None
    assignee_ids: list[UUID]
    resource_ids: list[UUID]


class PlantafelOut(Schema):
    date_from: date
    date_to: date
    # Mitarbeiter-Bahnen (aus job_assignment).
    resources: list[BoardResourceOut]
    # Ressourcen-Bahnen (Betriebsmittel aus resource.job_resource).
    resource_lanes: list[BoardResourceLaneOut]
    jobs: list[BoardJobOut]
    unassigned_count: int


@router.get("/plantafel", response=PlantafelOut)
def plantafel(
    request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    """Plantafel-Daten für einen Zeitraum: die Mitarbeiter-Bahnen (aus den
    Zuweisungen der Einsätze im Fenster), die Ressourcen-Bahnen (Betriebsmittel
    aus resource.job_resource) und die verplanten Einsätze mit ihren assignee_ids
    und resource_ids. Nur Einsätze mit Planbeginn erscheinen; Mehrfachzuweisungen
    tauchen in jeder betroffenen Bahn auf (n:m). Standardfenster: 7 Tage ab heute,
    maximal 45 Tage."""
    require(request, "workflow", "LESEN")
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
        .select_related("work_order__property", "property", "appointment_category")
        .prefetch_related("assignments__assignee", "resource_links__resource")
        .order_by("scheduled_start", "id")
    )

    resources: dict = {}
    resource_lanes: dict = {}
    unassigned = 0
    out_jobs = []
    for j in jobs:
        assignee_ids = []
        for a in j.assignments.all():
            resources[a.assignee_id] = a.assignee.display_name
            assignee_ids.append(a.assignee_id)
        resource_ids = []
        for link in j.resource_links.all():
            r = link.resource
            resource_lanes[r.id] = r
            resource_ids.append(r.id)
        if not assignee_ids and not resource_ids:
            unassigned += 1
        # Freier Termin: eigener Titel, Liegenschaft optional (kann fehlen).
        board_property = _job_property(j)
        out_jobs.append(
            BoardJobOut(
                id=j.id,
                job_number=j.job_number,
                title=_job_title(j),
                status=j.status,
                is_free=j.work_order_id is None,
                scheduled_start=j.scheduled_start,
                scheduled_end=j.scheduled_end,
                property_name=board_property.name if board_property else None,
                category=_category_ref(j),
                assignee_ids=assignee_ids,
                resource_ids=resource_ids,
            )
        )
    resource_list = [
        BoardResourceOut(id=uid, display_name=name)
        for uid, name in sorted(resources.items(), key=lambda kv: kv[1])
    ]
    lane_list = [
        BoardResourceLaneOut(
            id=r.id, display_name=r.name, resource_type=r.resource_type
        )
        for r in sorted(resource_lanes.values(), key=lambda r: r.name)
    ]
    return PlantafelOut(
        date_from=start,
        date_to=end,
        resources=resource_list,
        resource_lanes=lane_list,
        jobs=out_jobs,
        unassigned_count=unassigned,
    )


# ===========================================================================
# Terminkategorien (Stammdaten) — Modul workflow
# ===========================================================================
# row_scope-Entscheidung: Terminkategorien und Ressourcen sind Planungs-
# Stammdaten, die die Disposition/Leitung pflegt. Die Lese-Endpunkte nutzen
# `require` (fail-closed) — ein MONTEUR (Scope 'EIGENE') bekommt bewusst 403.
# Das ist korrekt, weil er diese Stammdaten nicht verwaltet; die Kategorie
# SEINES eigenen Einsatzes sieht er trotzdem, denn sie ist im
# (scope-geprueften) Einsatz-Detail eingebettet — er braucht die Stammdatenliste
# dafuer nicht. Konsistent mit list_assignable_users/plantafel (ebenfalls
# `require`).

class CategoryOut(Schema):
    id: UUID
    name: str
    description: str | None = None
    color_token: str
    status: str
    sort_order: int


class CategoryCreateIn(Schema):
    name: str
    color_token: str = "NAVY"
    description: str | None = None
    sort_order: int = 0


class CategoryUpdateIn(Schema):
    name: str | None = None
    color_token: str | None = None
    description: str | None = None
    sort_order: int | None = None


def _category_out(c):
    return CategoryOut(
        id=c.id,
        name=c.name,
        description=c.description,
        color_token=c.color_token,
        status=c.status,
        sort_order=c.sort_order,
    )


@router.get("/kategorien", response=list[CategoryOut])
def list_kategorien(request, include_archived: bool = Query(False)):
    """Terminkategorien (Stammdaten). Standardmaessig nur AKTIVE; fuer die
    Verwaltung liefert include_archived=true auch archivierte."""
    require(request, "workflow", "LESEN")
    qs = AppointmentCategory.objects.all()
    if not include_archived:
        qs = qs.filter(status="AKTIV")
    return [
        _category_out(c) for c in qs.order_by("sort_order", "name", "id")
    ]


@router.post("/kategorien", response={201: CategoryOut}, auth=django_auth)
def create_kategorie(request, payload: CategoryCreateIn):
    """Legt eine Terminkategorie an. `require` (fail-closed): Kategorien pflegt
    die Disposition/Leitung (ANLEGEN); Monteur-Scope 'EIGENE' -> 403."""
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        c = planung_service.create_category(
            actor,
            name=payload.name,
            color_token=payload.color_token,
            description=payload.description,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _category_out(c))


@router.patch("/kategorien/{category_id}", response=CategoryOut, auth=django_auth)
def update_kategorie(request, category_id: UUID, payload: CategoryUpdateIn):
    """Aendert eine Terminkategorie (Name/Farbe/Beschreibung/Sortierung)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        c = planung_service.update_category(
            actor,
            category_id=category_id,
            name=payload.name,
            color_token=payload.color_token,
            description=payload.description,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _category_out(c)


@router.post(
    "/kategorien/{category_id}/archivieren", response=CategoryOut, auth=django_auth
)
def archive_kategorie(request, category_id: UUID):
    """Archiviert eine Terminkategorie (statt Loeschen)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        c = planung_service.archive_category(actor, category_id=category_id)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _category_out(c)


# ===========================================================================
# Ressourcen (Betriebsmittel) — Schema resource.*, Modul workflow
# ===========================================================================

class ResourceOut(Schema):
    id: UUID
    resource_number: str
    name: str
    resource_type: str
    status: str
    notes: str | None = None


class ResourceCreateIn(Schema):
    name: str
    resource_type: str
    notes: str | None = None


class ResourceUpdateIn(Schema):
    name: str | None = None
    resource_type: str | None = None
    notes: str | None = None


class ResourceStatusIn(Schema):
    to_status: str


def _resource_out(r):
    return ResourceOut(
        id=r.id,
        resource_number=r.resource_number,
        name=r.name,
        resource_type=r.resource_type,
        status=r.status,
        notes=r.notes,
    )


@router.get("/ressourcen", response=list[ResourceOut])
def list_ressourcen(
    request,
    q: str | None = Query(None),
    resource_type: str | None = Query(None),
    include_inactive: bool = Query(False),
):
    """Ressourcen (Stammdaten/Betriebsmittel). Standardmaessig nur AKTIVE; fuer die
    Verwaltung liefert include_inactive=true auch INAKTIVE/ARCHIVIERTE."""
    require(request, "workflow", "LESEN")
    if resource_type and resource_type not in planung_service.RESOURCE_TYPES:
        raise HttpError(422, f"Unbekannter Typ '{resource_type}'.")
    qs = Resource.objects.all()
    if not include_inactive:
        qs = qs.filter(status="AKTIV")
    if resource_type:
        qs = qs.filter(resource_type=resource_type)
    if q:
        needle = q.strip()
        qs = qs.filter(
            Q(name__icontains=needle) | Q(resource_number__icontains=needle)
        )
    return [_resource_out(r) for r in qs.order_by("name", "id")[:500]]


@router.post("/ressourcen", response={201: ResourceOut}, auth=django_auth)
def create_ressource(request, payload: ResourceCreateIn):
    """Legt eine Ressource an. `require` (fail-closed): Betriebsmittel pflegt die
    Disposition/Leitung (ANLEGEN); Monteur-Scope 'EIGENE' -> 403."""
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        r = planung_service.create_resource(
            actor,
            name=payload.name,
            resource_type=payload.resource_type,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _resource_out(r))


@router.patch("/ressourcen/{resource_id}", response=ResourceOut, auth=django_auth)
def update_ressource(request, resource_id: UUID, payload: ResourceUpdateIn):
    """Aendert Name/Typ/Notiz einer Ressource (nicht den Status)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        r = planung_service.update_resource(
            actor,
            resource_id=resource_id,
            name=payload.name,
            resource_type=payload.resource_type,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _resource_out(r)


@router.post("/ressourcen/{resource_id}/status", response=ResourceOut, auth=django_auth)
def set_ressource_status(request, resource_id: UUID, payload: ResourceStatusIn):
    """Wechselt den Ressourcenstatus (AKTIV<->INAKTIV, INAKTIV->ARCHIVIERT).
    'ARCHIVIERT' entspricht dem Hero-Entfernen (nicht mehr einplanbar)."""
    actor, _ = require(request, "workflow", "AENDERN")
    try:
        r = planung_service.set_resource_status(
            actor, resource_id=resource_id, to_status=payload.to_status
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _resource_out(r)


# ===========================================================================
# Zuordnung Kategorie/Ressource <-> Einsatz
# ===========================================================================

class JobCategoryIn(Schema):
    category_id: UUID | None = None


class ResourceAssignIn(Schema):
    resource_id: UUID


class ResourceAssignOut(Schema):
    resource: ResourceRefOut
    # Nicht-blockierende Doppelbelegungs-Hinweise (die Zuordnung wurde angelegt).
    warnings: list[str] = []


@router.post("/einsaetze/{job_id}/kategorie", response=ServiceJobOut, auth=django_auth)
def set_einsatz_kategorie(request, job_id: UUID, payload: JobCategoryIn):
    """Setzt oder entfernt (category_id=null) die Terminkategorie eines Einsatzes.

    `require` (fail-closed): die Kategorisierung steuert die Disposition/Leitung
    (AENDERN); Monteur-Scope 'EIGENE' -> 403."""
    actor, _ = require(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    try:
        planung_service.set_job_category(
            actor, service_job_id=job_id, category_id=payload.category_id
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload_job(job_id)


@router.post(
    "/einsaetze/{job_id}/ressourcen",
    response={201: ResourceAssignOut},
    auth=django_auth,
)
def assign_ressource(request, job_id: UUID, payload: ResourceAssignIn):
    """Ordnet dem Einsatz eine Ressource zu (resource.job_resource).

    `require` (fail-closed): die Disposition/Leitung plant Betriebsmittel ein;
    Monteur-Scope 'EIGENE' -> 403. Doppelbelegung wird NICHT gesperrt — bei
    ueberlappenden bekannten Zeitfenstern kommen nicht-blockierende Warnhinweise
    im Feld `warnings` zurueck (die Zuordnung wird dennoch angelegt)."""
    actor, _ = require(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    try:
        link, warnings = planung_service.assign_resource(
            actor, service_job_id=job_id, resource_id=payload.resource_id
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    r = Resource.objects.get(id=link.resource_id)
    return Status(
        201,
        ResourceAssignOut(
            resource=ResourceRefOut(
                id=r.id,
                resource_number=r.resource_number,
                name=r.name,
                resource_type=r.resource_type,
            ),
            warnings=warnings,
        ),
    )


@router.delete(
    "/einsaetze/{job_id}/ressourcen/{resource_id}",
    response={200: dict},
    auth=django_auth,
)
def unassign_ressource(request, job_id: UUID, resource_id: UUID):
    """Entfernt eine Ressourcenzuordnung (nur vor Einsatzabschluss; danach 422)."""
    actor, _ = require(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    try:
        planung_service.unassign_resource(
            actor, service_job_id=job_id, resource_id=resource_id
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(200, {"detail": "Ressourcenzuordnung entfernt."})
