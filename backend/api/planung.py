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
from db_core.betriebszeit import Betriebszeitpunkt
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
    # Volle Zieladresse — der Monteur muss am Termin sehen, WOHIN er fahren muss
    # (HERO zeigt die Adresse fest auf jeder Terminkarte). street/postal_code sind
    # in property.address Pflicht; house_number ist optional.
    street: str | None = None
    house_number: str | None = None
    postal_code: str | None = None
    city: str
    # Präziser Ort innerhalb der Liegenschaft (Migration 0119). Bei mehreren
    # Adressen/Häusern je Liegenschaft benennt `building` das Haus, `unit` die
    # Wohnung/Einheit („3. OG rechts"). Beide als TEXT — WCAG: nie nur über
    # Farbe/Position. street/house_number oben sind dann die des Gebäudes, falls
    # es eine eigene Anschrift trägt.
    building: str | None = None
    unit: str | None = None


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
    # Seit Migration 0066 ist `ended_at` nullbar: NULL = die Stempeluhr läuft
    # noch. Ohne `| None` warf die Einsatz-Mappe für JEDEN Benutzer einen
    # pydantic-ValidationError (500), sobald jemand am Einsatz eingestempelt war.
    ended_at: datetime | None = None
    note: str | None = None
    user: str | None = None


class MaterialEntryOut(Schema):
    description: str
    quantity: Decimal
    unit: str
    note: str | None = None


class ScheduleOut(ServiceJobOut):
    """Umplanen-Antwort: der Einsatz plus NICHT-blockierende Doppelbelegungs-
    Hinweise für das neue Zeitfenster (Mitarbeiter und Ressourcen).

    Die Umplanung ist bereits geschrieben; `warnings` verhindert nichts. Die
    Doppelbelegung ist eine bewusst weiche Invariante (der maßgebliche Zeitraum
    liegt nullable am service_job) — sie wird sichtbar gemacht, nicht gesperrt.
    """

    warnings: list[str] = []


class AssignmentCreatedOut(AssignmentOut):
    """Zuweisungs-Antwort inkl. weicher Doppelbelegungs-Hinweise (siehe
    ScheduleOut). Die Zuweisung ist angelegt; die Warnung blockiert nicht."""

    warnings: list[str] = []


class ServiceJobDetailOut(ServiceJobOut):
    access_instructions: str | None = None
    completion_notes: str | None = None
    on_site_contact: str | None = None
    # Die ROHEN, bearbeitbaren Werte — Gegenstück zu den aufgelösten Anzeigefeldern
    # oben. Ein Bearbeiten-Formular MUSS sie kennen, sonst schickt es beim
    # Speichern zurück, was es gerade sieht, statt was gespeichert ist:
    # * `on_site_contact` ist ein Anzeigename; wer den Kontakt behalten will,
    #   braucht seine ID.
    # * `title` ist der AUFGELÖSTE Titel (beim Auftragstermin der Auftragstitel).
    #   Wer ihn zurückschriebe, brennte den Auftragstitel in den Einsatz ein — er
    #   folgte einer späteren Auftragsumbenennung nicht mehr. `own_title` ist der
    #   eigene Titel und darf NULL sein.
    on_site_contact_party_id: UUID | None = None
    own_title: str | None = None
    # Die ROHEN eigenen Ortsangaben des Einsatzes (0119) — Gegenstück zu den
    # aufgelösten Labels in `property`. Das Bearbeiten-Formular MUSS sie kennen:
    # `property.building`/`property.unit` sind Anzeigetexte und beim
    # auftragsgebundenen Termin ggf. vom AUFTRAG geerbt; wer das Formular damit
    # zurückschriebe, brennte einen geerbten Ort in den Einsatz ein. Diese Felder
    # sind der eigene Ort des Einsatzes (NULL, wenn er vom Auftrag erbt).
    own_property_id: UUID | None = None
    own_building_id: UUID | None = None
    own_unit_id: UUID | None = None
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
    scheduled_from: Betriebszeitpunkt | None = None
    scheduled_to: Betriebszeitpunkt | None = None


class ServiceJobCreateIn(Schema):
    # Ohne work_order_id entsteht ein FREIER TERMIN; dann ist `title` Pflicht
    # (der Service prüft das, 422). Mit work_order_id ist `title` optional
    # (Fallback: Auftragstitel) und `property_id` muss zum Auftrag passen.
    work_order_id: UUID | None = None
    title: str | None = None
    property_id: UUID | None = None
    # Präziser Ort (0119): Gebäude/Einheit setzen eine Liegenschaft voraus und
    # müssen zu ihr passen (Service prüft vorab, 422). Beim freien Termin die
    # einzige Möglichkeit, das konkrete Haus/die Wohnung zu treffen.
    building_id: UUID | None = None
    unit_id: UUID | None = None
    scheduled_start: Betriebszeitpunkt | None = None
    scheduled_end: Betriebszeitpunkt | None = None
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
    building_id: UUID | None = None
    unit_id: UUID | None = None
    access_instructions: str | None = None


class ScheduleIn(Schema):
    scheduled_start: Betriebszeitpunkt
    scheduled_end: Betriebszeitpunkt | None = None


class StatusAdvanceIn(Schema):
    to_status: str
    reason: str | None = None


class AssignmentIn(Schema):
    assignee_user_id: UUID
    role: str = "TECHNICIAN"


class TimeLogIn(Schema):
    time_type: str
    started_at: Betriebszeitpunkt
    ended_at: Betriebszeitpunkt
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


def _job_ort(job):
    """Ort des Einsatzes als Tripel (Liegenschaft, Gebäude, Einheit).

    Der Einsatz kann selbst ein Gebäude/eine Einheit tragen (freier Termin oder
    präzisierter Ort, Migration 0119). Trägt er keines, erbt die Anzeige den
    präzisen Ort vom Auftrag. Alle Wege sind per select_related geladen (kein N+1);
    wo Werte gesetzt sind, sind sie laut DB-FK konsistent zur Liegenschaft.
    """
    p = _job_property(job)
    if job.building_id is not None:
        return p, job.building, job.unit
    order = job.work_order
    if order is not None and order.building_id is not None:
        return p, order.building, order.unit
    return p, None, None


def _ort_address(prop, building):
    """Die anzuzeigende Anschrift: die des Gebäudes, sonst die der Liegenschaft.

    `building.address` ist NULL-fähig (0004) — mehrere Gebäude einer Liegenschaft
    ohne je eigene Anschrift teilen dann die Liegenschaftsadresse.
    """
    if building is not None and building.address_id is not None:
        return building.address
    return prop.address if prop is not None else None


def _building_label(building):
    if building is None:
        return None
    return building.name or f"Gebäude {building.building_number}"


def _unit_label(unit):
    return unit.unit_number if unit is not None else None


def _property_ref(job):
    prop, building, unit = _job_ort(job)
    if prop is None:
        return None
    a = _ort_address(prop, building)
    return PropertyRefOut(
        id=prop.id,
        property_number=prop.property_number,
        name=prop.name,
        # Straße/PLZ/Ort stammen aus der GEBÄUDEadresse, falls das Gebäude eine
        # eigene hat — sonst aus der Liegenschaft. So zeigt die Karte „wohin".
        street=a.street if a else None,
        house_number=a.house_number if a else None,
        postal_code=a.postal_code if a else None,
        city=a.city if a else "",
        building=_building_label(building),
        unit=_unit_label(unit),
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
        "work_order__property__address", "property__address", "appointment_category",
        "building__address", "unit",
        "work_order__building__address", "work_order__unit",
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
            "building__address",
            "unit",
            "work_order__building__address",
            "work_order__unit",
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
        # `category` mit laden: die `time_type`-Property (0066) liest die
        # Kategorie — sonst eine Extra-Query je Zeile.
        TimeEntry.objects.filter(service_job_id=job.id)
        .select_related("user", "category")
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
        on_site_contact_party_id=job.on_site_contact_party_id,
        own_title=job.title,
        own_property_id=job.property_id,
        own_building_id=job.building_id,
        own_unit_id=job.unit_id,
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
            "building__address",
            "unit",
            "work_order__building__address",
            "work_order__unit",
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
            building_id=payload.building_id,
            unit_id=payload.unit_id,
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


@router.post("/einsaetze/{job_id}/schedule", response=ScheduleOut, auth=django_auth)
def set_schedule(request, job_id: UUID, payload: ScheduleIn):
    """Setzt/ändert den Planungszeitraum eines Einsatzes (ohne Statuswechsel).
    Speist auch das Verschieben einer Kachel auf der Plantafel (Drag & Drop).

    `require` (fail-closed): Umplanen ist Dispositionssache; der Monteur (Scope
    'EIGENE') bekommt 403. Der DB-CHECK verlangt scheduled_end > scheduled_start
    (→ 422).

    Antwort enthält `warnings`: Doppelbelegung von Mitarbeitern/Ressourcen im
    NEUEN Zeitfenster. Diese Hinweise blockieren NICHT (weiche Invariante) — das
    UI zeigt sie an, die Umplanung ist bereits geschrieben."""
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
    base = _reload_job(job_id)
    return ScheduleOut(
        **base.dict(), warnings=planung_service.belegungs_warnungen(job_id)
    )


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
    "/einsaetze/{job_id}/assignments",
    response={201: AssignmentCreatedOut},
    auth=django_auth,
)
def assign_user(request, job_id: UUID, payload: AssignmentIn):
    """Weist dem Einsatz einen Mitarbeiter zu.

    `require` (fail-closed): Wer wen einplant, entscheidet die Disposition/Leitung
    — ein Monteur darf sich (oder andere) nicht selbst zuweisen, sonst könnte er
    sich fremde Einsätze über die 'EIGENE'-Grenze holen; Scope 'EIGENE' → 403.

    Antwort enthält `warnings` (Doppelbelegung im Zeitfenster) — nicht blockierend,
    die Zuweisung ist angelegt."""
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
        AssignmentCreatedOut(
            assignee_id=assignment.assignee_id,
            display_name=assignment.assignee.display_name,
            role=assignment.role,
            warnings=planung_service.belegungs_warnungen(job_id),
        ),
    )


@router.delete(
    "/einsaetze/{job_id}/assignments/{assignee_user_id}",
    response={200: dict},
    auth=django_auth,
)
def unassign_user(request, job_id: UUID, assignee_user_id: UUID):
    """Hebt die Zuweisung eines Mitarbeiters am Einsatz auf (Korrektur/Umplanung).

    Gegenstück zu assign_user; wird u. a. gebraucht, wenn eine Kachel auf der
    Plantafel von einer Mitarbeiter-Bahn in eine andere gezogen wird.

    `require` (fail-closed) wie assign_user: Zuweisungen steuert die Disposition —
    ein Monteur (Scope 'EIGENE') könnte sich sonst von einem unliebsamen Einsatz
    selbst abmelden; Scope 'EIGENE' → 403. Nach Einsatzabschluss sperrt der
    DB-Trigger das Lösen (Historienschutz F-02) → 422."""
    actor, _ = require(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    try:
        einsatz_service.unassign_user(
            actor, service_job_id=job_id, assignee_user_id=assignee_user_id
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(200, {"detail": "Zuweisung aufgehoben."})


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
    entry = TimeEntry.objects.select_related("user", "category").get(id=entry.id)
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

class BoardLaneOut(Schema):
    """Eine Schwimmbahn: Mitarbeiter ODER Betriebsmittel.

    `plan_hours`/`target_hours` sind die Auslastung im angezeigten Zeitraum.
    `target_hours` ist **null**, wenn kein gültiger Arbeitsvertrag existiert — das
    heißt „unbekannt", NICHT „null Stunden Soll" (sonst sähe jeder Mitarbeiter
    ohne Vertrag maximal überlastet aus). Betriebsmittel haben kein Soll.
    """

    kind: str  # USER | RESOURCE
    id: UUID
    display_name: str
    sub: str | None = None
    plan_hours: Decimal | None = None
    target_hours: Decimal | None = None


class KonfliktOut(Schema):
    """Nicht-blockierender Konflikt an einer Kachel.

    `kind` ∈ DOPPELBELEGUNG | ABWESENHEIT | FEIERTAG | OFFENES_ENDE |
    QUALIFIKATION. Doppelbelegung ist eine bewusst **weiche** Invariante
    (Migration 0025), die fehlende Qualifikation ebenso (Migration 0078): Die
    Plantafel macht sie sichtbar, die DB verbietet sie nicht — der Notdienst am
    Sonntag darf nicht an einem gesperrten Board scheitern. `text` ist immer
    gesetzt — das UI zeigt Text + Symbol, nie nur Farbe (WCAG 1.4.1).
    """

    kind: str
    text: str


def _ort_adresse_kurz(prop, building, unit):
    """Kompakte Zieladresse für die Board-/Rückstands-Kachel: „Straße Hausnr,
    Stadt" (ohne PLZ, damit die Kachel schmal bleibt), plus präziser Ort. HERO
    zeigt die Adresse fest auf der Terminkachel — der Disponent sieht so ohne Klick,
    wo der Einsatz ist.

    Straße/Ort stammen aus der Gebäudeadresse, falls das Gebäude eine eigene hat
    (0119). Das Gebäude wird nur EXTRA genannt, wenn es KEINE eigene Anschrift hat
    (sonst steckt es schon in der Straße); die Einheit („3. OG rechts") immer.
    """
    a = _ort_address(prop, building)
    if a is None:
        return None
    strasse = " ".join(t for t in (a.street, a.house_number) if t and t.strip())
    teile = [t for t in (strasse, a.city) if t and t.strip()]
    basis = ", ".join(teile) or a.city
    extra = []
    if building is not None and building.address_id is None:
        extra.append(_building_label(building))
    if unit is not None:
        extra.append(_unit_label(unit))
    return f"{basis} · {' · '.join(extra)}" if extra else basis


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
    # Kompakte Zieladresse (Straße, Stadt) — „wo ist der Einsatz" auf der Kachel.
    property_address: str | None = None
    category: CategoryRefOut | None = None
    assignee_ids: list[UUID]
    resource_ids: list[UUID]
    conflicts: list[KonfliktOut] = []
    # Herkunftsklammer einer Serie (Migration 0077). null = Einzeltermin. Jedes
    # Vorkommen ist ein eigenständiger Einsatz — die Klammer ist nur Anzeige.
    series_id: UUID | None = None


def _board_job_out(j, *, konflikte=()):
    """Eine Board-Kachel. Einzige Abbildungsstelle — Board und Serie nutzen sie."""
    prop, building, unit = _job_ort(j)
    return BoardJobOut(
        id=j.id,
        job_number=j.job_number,
        title=_job_title(j),
        status=j.status,
        is_free=j.work_order_id is None,
        scheduled_start=j.scheduled_start,
        scheduled_end=j.scheduled_end,
        property_name=prop.name if prop else None,
        property_address=_ort_adresse_kurz(prop, building, unit),
        category=_category_ref(j),
        assignee_ids=[a.assignee_id for a in j.assignments.all()],
        resource_ids=[link.resource_id for link in j.resource_links.all()],
        conflicts=[KonfliktOut(**k) for k in konflikte],
        series_id=j.series_id,
    )


class BacklogJobOut(Schema):
    """Ein UNGEPLANTER Einsatz — der Rückstand, den man ins Raster zieht."""

    id: UUID
    job_number: str
    title: str
    status: str
    is_free: bool = False
    property_name: str | None = None
    property_address: str | None = None
    category: CategoryRefOut | None = None
    order_number: str | None = None


class BoardAbsenceOut(Schema):
    """Genehmigte Abwesenheit — Sperrfläche in der Mitarbeiter-Bahn.

    **Ohne Abwesenheitsart.** Die Art (Urlaub/Krankheit/…) ist eine besondere
    Kategorie nach DSGVO Art. 9 und hängt am `hr`-Tor (api/mitarbeiter.py). Die
    Plantafel hängt an `workflow`/LESEN — ein Disponent OHNE hr-Recht darf hier
    nicht erfahren, wer krank ist. Für die Disposition genügt „abwesend, von–bis":
    Das Feld ist gesperrt, der Grund geht ihn nichts an.
    """

    id: UUID
    app_user_id: UUID
    start_date: date
    end_date: date


class BoardHolidayOut(Schema):
    holiday_date: date
    name: str


class PlantafelOut(Schema):
    date_from: date
    date_to: date
    lanes: list[BoardLaneOut]
    jobs: list[BoardJobOut]
    backlog: list[BacklogJobOut]
    backlog_total: int
    absences: list[BoardAbsenceOut]
    # Aus `hr.holiday`; leer, solange der Betrieb dort nichts gepflegt hat — das
    # Board erfindet keine Feiertage.
    holidays: list[BoardHolidayOut] = []
    unassigned_count: int


@router.get("/plantafel", response=PlantafelOut)
def plantafel(
    request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    q: str | None = Query(None),
    category_id: UUID | None = Query(None),
    backlog_q: str | None = Query(None),
):
    """Plantafel-Daten für einen Zeitraum — Bahnen, Kacheln, Rückstand, Sperrflächen.

    * **Bahnen** sind ALLE aktiven Mitarbeiter und Betriebsmittel, nicht nur die
      bereits verplanten: Auf eine leere Bahn muss man ziehen können.
    * **Kacheln** sind alle Einsätze, deren Zeitraum das Fenster **überlappt** —
      ein mehrtägiger Einsatz erscheint an jedem seiner Tage, nicht nur am Start.
    * **Rückstand** (`backlog`) sind die UNGEPLANTEN Einsätze (ohne Planbeginn).
    * **Sperrflächen** sind genehmigte Abwesenheiten und Feiertage.
    * **Konflikte** hängen an der Kachel und blockieren nichts.

    Standardfenster: 7 Tage ab heute, maximal 45 Tage."""
    require(request, "workflow", "LESEN")
    today = date.today()
    start = date_from or today
    end = date_to or (start + timedelta(days=6))
    if end < start:
        raise HttpError(422, "date_to darf nicht vor date_from liegen.")
    if (end - start).days > planung_service.MAX_BOARD_TAGE:
        raise HttpError(
            422,
            f"Der Zeitraum darf höchstens {planung_service.MAX_BOARD_TAGE} Tage "
            "umfassen.",
        )

    board = planung_service.board_daten(
        date_from=start,
        date_to=end,
        q=q,
        category_id=category_id,
        backlog_q=backlog_q,
    )

    jobs = [
        _board_job_out(j, konflikte=board.konflikte.get(j.id, []))
        for j in board.jobs
    ]

    backlog = []
    for j in board.backlog:
        prop, building, unit = _job_ort(j)
        backlog.append(
            BacklogJobOut(
                id=j.id,
                job_number=j.job_number,
                title=_job_title(j),
                status=j.status,
                is_free=j.work_order_id is None,
                property_name=prop.name if prop else None,
                property_address=_ort_adresse_kurz(prop, building, unit),
                category=_category_ref(j),
                order_number=(
                    j.work_order.order_number if j.work_order_id else None
                ),
            )
        )

    return PlantafelOut(
        date_from=board.date_from,
        date_to=board.date_to,
        lanes=[BoardLaneOut(**lane) for lane in board.lanes],
        jobs=jobs,
        backlog=backlog,
        backlog_total=board.backlog_total,
        absences=[BoardAbsenceOut(**a) for a in board.absences],
        holidays=[
            BoardHolidayOut(holiday_date=d, name=n) for d, n in board.holidays
        ],
        unassigned_count=board.unassigned_count,
    )


class AbwesendOut(Schema):
    """„Wer ist gerade nicht da" — für die Disposition.

    **Ohne Abwesenheitsart, mit voller Absicht.** Die Art (Urlaub? Krankheit?)
    ist ein Gesundheitsdatum und damit eine besondere Kategorie nach DSGVO
    Art. 9. Diese Ansicht hängt an `workflow/LESEN` — das Recht der Disposition,
    die kein `hr` hat. Sie beantwortet deshalb genau eine Frage: **wer fehlt,
    von wann bis wann**. Der Grund geht die Planung nichts an; wer ihn kennen
    darf, holt ihn über das hr-Tor (`/api/hr/absences`, `/hr/abwesenheiten.csv`).

    Genau dieser Fehler — die Art in einer Planungssicht mitzuliefern — wurde in
    der Plantafel schon einmal gefunden und behoben (`BoardAbsenceOut`). Er wird
    hier nicht wiederholt.
    """

    id: UUID
    app_user_id: UUID
    name: str
    start_date: date
    end_date: date
    half_day_start: bool
    half_day_end: bool


@router.get("/abwesend", response=list[AbwesendOut])
def abwesend(
    request,
    von: date | None = Query(None),
    bis: date | None = Query(None),
):
    """Genehmigte Abwesenheiten im Zeitraum (Default: heute).

    Nur GENEHMIGTE: ein eingereichter Antrag ist noch keine Tatsache — und ihn
    hier zu zeigen, verriete eine Krankmeldung, bevor sie überhaupt beschieden
    ist.
    """
    require(request, "workflow", "LESEN")
    heute = date.today()
    v = von or heute
    b = bis or v
    if b < v:
        raise HttpError(422, "Das Ende des Zeitraums liegt vor dem Beginn.")
    if (b - v).days > 366:
        raise HttpError(422, "Der Zeitraum darf höchstens ein Jahr umfassen.")
    return [AbwesendOut(**a) for a in planung_service.abwesend_im_zeitraum(v, b)]


# --- Termin anlegen/ändern aus dem Board (ein Vorgang) ----------------------

class TerminCreateIn(Schema):
    """Ein Termin mit allem, was am Board dranhängt — in EINEM Aufruf.

    Ohne `work_order_id` entsteht ein freier Termin (dann ist `title` Pflicht).
    Ohne `scheduled_start` landet der Termin im **Rückstand** (Status UNGEPLANT) —
    das ist gewollt, nicht versehentlich.
    """

    work_order_id: UUID | None = None
    title: str | None = None
    property_id: UUID | None = None
    building_id: UUID | None = None
    unit_id: UUID | None = None
    scheduled_start: Betriebszeitpunkt | None = None
    scheduled_end: Betriebszeitpunkt | None = None
    on_site_contact_party_id: UUID | None = None
    access_instructions: str | None = None
    appointment_category_id: UUID | None = None
    assignee_ids: list[UUID] = []
    resource_ids: list[UUID] = []
    # Gewerk (0120). Ohne Angabe erbt der auftragsgebundene Termin das Gewerk
    # seines Auftrags — ein Termin zum Heizungsauftrag ist ein Heizungstermin.
    trade_id: UUID | None = None


class TerminUpdateIn(Schema):
    """Teil-Update. Nur mitgeschickte Felder werden geändert; ein ausdrückliches
    ``null`` löscht (Kategorie/Kontakt entfernen). `assignee_ids`/`resource_ids`
    sind eine **Vollersetzung**: Was fehlt, wird gelöst.

    Ein ausdrückliches ``"scheduled_start": null`` legt den Termin **zurück in den
    Rückstand** (Zeitraum weg, Status GEPLANT → UNGEPLANT). Dieser Statuswechsel
    ist begründungspflichtig → `reason` ist dann Pflicht (sonst 422).

    Der Auftragsbezug fehlt bewusst — er ist in der DB unveränderlich (WF-01).
    """

    title: str | None = None
    property_id: UUID | None = None
    building_id: UUID | None = None
    unit_id: UUID | None = None
    scheduled_start: Betriebszeitpunkt | None = None
    scheduled_end: Betriebszeitpunkt | None = None
    on_site_contact_party_id: UUID | None = None
    access_instructions: str | None = None
    appointment_category_id: UUID | None = None
    assignee_ids: list[UUID] | None = None
    resource_ids: list[UUID] | None = None
    # Begründung für den Statuswechsel GEPLANT → UNGEPLANT (Rückweg in den
    # Rückstand). Kein Feld am Einsatz, sondern der `status_reason` des Audits.
    reason: str | None = None


class TerminOut(ServiceJobOut):
    """Der geschriebene Termin plus die nicht-blockierenden Belegungshinweise."""

    warnings: list[str] = []


@router.post("/termine", response={201: TerminOut}, auth=django_auth)
def create_termin(request, payload: TerminCreateIn):
    """Legt einen Termin samt Kategorie, Mitarbeitern und Betriebsmitteln an.

    Der ganze Vorgang läuft in EINER Transaktion: Vorher hätte das Board vier bis
    acht Einzelrufe absetzen müssen und bei einem Fehler in der Mitte einen halb
    angelegten Termin hinterlassen.

    `require` (fail-closed) wie create_einsatz: Termine plant die Disposition;
    Monteur-Scope 'EIGENE' → 403. Die DB-Tore kommen als 422."""
    actor, _ = require(request, "workflow", "ANLEGEN")
    require(request, "workflow", "AENDERN")  # Zuweisung/Status gehören dazu
    try:
        job = planung_service.create_termin(
            actor,
            work_order_id=payload.work_order_id,
            title=payload.title,
            property_id=payload.property_id,
            building_id=payload.building_id,
            unit_id=payload.unit_id,
            scheduled_start=payload.scheduled_start,
            scheduled_end=payload.scheduled_end,
            on_site_contact_party_id=payload.on_site_contact_party_id,
            access_instructions=payload.access_instructions,
            appointment_category_id=payload.appointment_category_id,
            assignee_ids=payload.assignee_ids,
            resource_ids=payload.resource_ids,
            trade_id=payload.trade_id,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    base = _reload_job(job.id)
    return Status(
        201,
        TerminOut(
            **base.dict(), warnings=planung_service.belegungs_warnungen(job.id)
        ),
    )


@router.patch("/termine/{job_id}", response=TerminOut, auth=django_auth)
def update_termin(request, job_id: UUID, payload: TerminUpdateIn):
    """Ändert einen Termin vollständig aus dem Board heraus (ein Vorgang).

    `require` (fail-closed): Umplanen/Zuweisen ist Dispositionssache; Monteur-Scope
    'EIGENE' → 403 (er trägt am eigenen freien Termin über PATCH /einsaetze/{id}
    Kontakt und Zutrittshinweise nach — dieser Endpunkt darf mehr und ist deshalb
    strenger).

    `scheduled_start: null` ist die **Gegenbewegung zum Ziehen ins Raster**: Der
    Termin geht zurück in den Rückstand (Statuswechsel GEPLANT → UNGEPLANT, mit
    `reason` begründungspflichtig)."""
    actor, _ = require(request, "workflow", "AENDERN")
    _load_job_or_404(job_id)
    gesetzt = payload.model_fields_set
    # `assignee_ids`/`resource_ids` und `reason` werden benannt übergeben — sie
    # sind kein Sentinel-Feld des Einsatzes.
    felder = {
        name: getattr(payload, name)
        for name in gesetzt
        if name not in ("assignee_ids", "resource_ids", "reason")
    }
    try:
        planung_service.update_termin(
            actor,
            service_job_id=job_id,
            assignee_ids=payload.assignee_ids,
            resource_ids=payload.resource_ids,
            reason=payload.reason,
            **felder,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    base = _reload_job(job_id)
    return TerminOut(
        **base.dict(), warnings=planung_service.belegungs_warnungen(job_id)
    )


class SerieIn(Schema):
    """Einen geplanten Termin wiederholen (Migration 0077)."""
    # TAEGLICH | WOECHENTLICH | ZWEIWOECHENTLICH | MONATLICH
    intervall: str
    # Zahl der ZUSÄTZLICHEN Termine (der Ausgangstermin bleibt der erste).
    anzahl: int
    # Fällt ein Vorkommen auf Sonntag/Feiertag, auf den nächsten Werktag schieben.
    # Der TAKT zählt trotzdem vom unverschobenen Datum weiter — sonst wanderte
    # „jeden Montag" nach dem ersten Feiertag dauerhaft auf den Dienstag.
    werktags: bool = True


class SerienTerminOut(Schema):
    """Ein Vorkommen einer Serie.

    **Eigenes Schema statt `BoardJobOut`**, weil `scheduled_start` hier
    NULL-fähig sein MUSS: Jedes Vorkommen ist ein eigenständiger Einsatz und darf
    einzeln in den Rückstand zurückgelegt werden (`scheduled_start = null`). Es
    bleibt trotzdem Teil der Reihe — es aus der Serienansicht zu filtern, hieße
    die Absage zu verschweigen.
    """
    id: UUID
    job_number: str
    title: str
    status: str
    is_free: bool = False
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    property_name: str | None = None
    category: CategoryRefOut | None = None
    series_id: UUID | None = None


class SerieOut(Schema):
    series_id: UUID
    # Die neu erzeugten Termine (der Ausgangstermin ist nicht dabei).
    erzeugt: list[SerienTerminOut]
    anzahl: int
    # Nicht-blockierende Belegungshinweise der NEUEN Termine (Doppelbelegung,
    # Abwesenheit, Feiertag) — je Termin gesammelt. Sie verhindern nichts (offene
    # Invariante), aber sie dürfen auch nicht stumm bleiben: Eine Serie legt
    # Termine in eine Zukunft, die der Disponent nicht vor Augen hat.
    warnungen: list[str] = []


def _serien_termin_out(j):
    # Nur der Liegenschaftsname wird gezeigt — bewusst _job_property (nicht
    # _job_ort): die Serien-Query lädt building/unit nicht, ein _job_ort-Zugriff
    # auf order.building/unit löste je Termin eine Lazy-Query aus.
    prop = _job_property(j)
    return SerienTerminOut(
        id=j.id,
        job_number=j.job_number,
        title=_job_title(j),
        status=j.status,
        is_free=j.work_order_id is None,
        scheduled_start=j.scheduled_start,
        scheduled_end=j.scheduled_end,
        property_name=prop.name if prop else None,
        category=_category_ref(j),
        series_id=j.series_id,
    )


@router.post("/termine/{job_id}/serie", response={201: SerieOut}, auth=django_auth)
def termin_wiederholen(request, job_id: UUID, payload: SerieIn):
    """Wiederholt einen Termin — als echte, eigenständige Folgetermine.

    Jedes Vorkommen ist ein eigener Einsatz mit eigener Nummer und eigenem Status
    (kein virtuelles Vorkommen): Ein abgesagter Dienstag macht den Mittwoch nicht
    kaputt. Mitarbeiter, Ressourcen, Kategorie und Dauer werden mitkopiert.
    """
    actor, _ = require(request, "workflow", "ANLEGEN")
    require(request, "workflow", "AENDERN")  # Zuweisung/Status gehören dazu
    _load_job_or_404(job_id)
    try:
        ergebnis = planung_service.serie_anlegen(
            actor,
            service_job_id=job_id,
            intervall=payload.intervall,
            anzahl=payload.anzahl,
            werktags=payload.werktags,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    jobs = [_serien_termin_out(j) for j in ergebnis["erzeugt"]]
    # Die Warnungen der NEUEN Termine mitliefern: Sie liegen in einer Zukunft,
    # die der Disponent gerade nicht auf dem Board sieht.
    warnungen = []
    # Weniger Termine als bestellt? Das muss AUSGESPROCHEN werden. Wer 6 eintippt
    # und 4 bekommt, soll nicht rätseln, ob etwas schiefging.
    entfallen = payload.anzahl - len(jobs)
    if entfallen > 0:
        warnungen.append(
            f"{entfallen} Vorkommen fiel{'en' if entfallen > 1 else ''} mit einem "
            "bestehenden Termin der Reihe zusammen und entfiel"
            f"{'en' if entfallen > 1 else ''}."
        )
    for j in ergebnis["erzeugt"]:
        for w in planung_service.belegungs_warnungen(j.id):
            warnungen.append(f"{j.job_number}: {w}")
    return Status(
        201,
        SerieOut(
            series_id=ergebnis["series_id"],
            erzeugt=jobs,
            anzahl=len(jobs),
            warnungen=warnungen,
        ),
    )


@router.get("/termine/{job_id}/serie", response=list[SerienTerminOut])
def serie_lesen(request, job_id: UUID):
    """Alle Termine der Serie, zu der dieser Einsatz gehört (chronologisch).

    Leere Liste, wenn er zu keiner Serie gehört. Ein Vorkommen, das in den
    Rückstand zurückgelegt wurde, trägt `scheduled_start: null` und bleibt Teil
    der Reihe — die Absage wird nicht verschwiegen.
    """
    require(request, "workflow", "LESEN")
    _load_job_or_404(job_id)
    return [_serien_termin_out(j) for j in planung_service.serie(job_id)]


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
    # Übliche Dauer dieses Termintyps (Minuten). **Nur ein Vorschlag** für den
    # Termin-Dialog; null = keiner. Der Server leitet daraus nie ein Ende ab.
    default_duration_minutes: int | None = None


class CategoryCreateIn(Schema):
    name: str
    color_token: str = "NAVY"
    description: str | None = None
    sort_order: int = 0
    default_duration_minutes: int | None = None


class CategoryUpdateIn(Schema):
    name: str | None = None
    color_token: str | None = None
    description: str | None = None
    sort_order: int | None = None
    default_duration_minutes: int | None = None


def _category_out(c):
    return CategoryOut(
        id=c.id,
        name=c.name,
        description=c.description,
        color_token=c.color_token,
        status=c.status,
        sort_order=c.sort_order,
        default_duration_minutes=c.default_duration_minutes,
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
            default_duration_minutes=payload.default_duration_minutes,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _category_out(c))


@router.patch("/kategorien/{category_id}", response=CategoryOut, auth=django_auth)
def update_kategorie(request, category_id: UUID, payload: CategoryUpdateIn):
    """Aendert eine Terminkategorie (Name/Farbe/Beschreibung/Sortierung/Dauer)."""
    actor, _ = require(request, "workflow", "AENDERN")
    gesetzt = payload.model_dump(exclude_unset=True)
    try:
        c = planung_service.update_category(
            actor,
            category_id=category_id,
            name=payload.name,
            color_token=payload.color_token,
            description=payload.description,
            sort_order=payload.sort_order,
            # Sentinel: „nicht mitgeschickt" (nicht ändern) vs. ausdrückliches
            # null („keine übliche Dauer mehr").
            default_duration_minutes=(
                payload.default_duration_minutes
                if "default_duration_minutes" in gesetzt
                else planung_service.UNSET
            ),
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
