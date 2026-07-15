"""Projekt-API — Projekte (workflow.project) inkl. verknüpfter Liegenschaften
und Vorgänge (service_case).

Views bleiben dünn, rufen die Service-Schicht; Model-Instanzen verlassen die API
nicht.

**row_scope 'EIGENE' (Objektsicht, Migration 0099) — nur LESEN.**
Der Vorgang „Heizkörper leckt" von vorgestern gehört zur Objekthistorie, die der
Monteur braucht. Also:

  * **Lesen** (Projekte, Vorgänge, Board, Logbuch, Checklisten, Übergänge): begrenzt
    auf meine Objekte. Der Projektbezug läuft über `workflow.project_property` — ein
    Projekt ist „meins", wenn **mindestens eine** seiner Liegenschaften meine ist.
  * **Jeder Schreibpfad** (Projekt/Vorgang anlegen, Statuswechsel, Logbuch,
    Checkliste, Schnellerfassung, Hochstufen) bleibt `require` → **403** bei 'EIGENE'.
"""
from datetime import date, datetime
from uuid import UUID

from django.db.models import Q
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.objektgrenze import guard_objekt, guard_projekt, verbiete_eigene
from api.permissions import require, require_scoped
from db_core.db_context import run_business_transaction
from db_core.models import (
    Checklist,
    Project,
    ProjectCategory,
    ProjectLog,
    ServiceCase,
    StatusChange,
)
from db_core.services import identity as identity_service
from db_core.services import objektsicht
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service

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


class ContactCardOut(Schema):
    """Kompakter Hauptkontakt eines Projekts (Eigentümer der ersten Liegenschaft).

    Abgeleitet, keine eigene Entität: der aktuelle PROPERTY_OWNER der ersten
    zugeordneten Liegenschaft plus seine primären Kontaktwege (E-Mail/Telefon)
    für mailto:/tel:. `email`/`phone` bleiben null, wenn kein Kontaktweg hinterlegt
    ist — die Karte zeigt dann nur Name und Objektbezug.
    """
    party_id: UUID
    display_name: str
    property_id: UUID
    property_name: str
    role: str
    email: str | None = None
    phone: str | None = None


class ProjectDetailOut(ProjectOut):
    version: int
    created_at: datetime
    updated_at: datetime
    properties: list[PropertyRefOut]
    service_cases: list[ServiceCaseOut]
    # Abgeleiteter Hauptkontakt (Eigentümer der ersten Liegenschaft), damit der
    # Kunde von der Projektübersicht aus direkt erreichbar ist — ohne den Umweg
    # Liegenschaft → Eigentümer. None, wenn keine Liegenschaft/kein Eigentümer.
    primary_contact: ContactCardOut | None = None


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


# --- Zeilenbegrenzung ('EIGENE') -------------------------------------------

def _eigene_objekte(scope, actor):
    """Set meiner property_ids — oder None bei Scope ALLE (= nicht filtern).

    Einmal je Request materialisiert; die Alternative wäre, dieselbe Subquery in
    jeder Listen-Comprehension erneut zu fahren.
    """
    if scope != "EIGENE":
        return None
    return {
        r["objekt_id"] for r in objektsicht.eigene_property_ids(actor)
    }


def _guard_vorgang(case_id, actor, scope):
    """Scope 'EIGENE': Der Vorgang muss an einem meiner Objekte hängen, sonst 404."""
    if scope != "EIGENE":
        return
    prop_id = (
        ServiceCase.objects.filter(id=case_id)
        .values_list("property_id", flat=True)
        .first()
    )
    guard_objekt(scope, actor, prop_id, "Vorgang nicht gefunden.")


# --- Lesende Endpoints ------------------------------------------------------

@router.get("/projects", response=ProjectListOut)
def list_projects(
    request,
    filters: ProjectFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Projekte auflisten: Suche (Name/Nummer), Status-/Kategoriefilter, Seiten.

    Scope 'EIGENE': nur Projekte, die **mindestens eine** meiner Liegenschaften
    tragen (`workflow.project_property`). `distinct()`, weil ein Projekt über mehrere
    meiner Objekte laufen kann — sonst stünde es mehrfach in der Liste und `total`
    zählte es doppelt.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    qs = Project.objects.select_related("category")
    if scope == "EIGENE":
        qs = objektsicht.begrenzen(
            qs, scope, actor, "property_links__property_id"
        ).distinct()

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


@router.get("/project-categories", response=list[CategoryOut])
def list_project_categories(request):
    """Aktive Projektkategorien (Gewerk/Ordner) für Auswahllisten im Anlagedialog.

    Read-only Stammdaten ohne Zeilenbezug: `require_scoped` mit workflow.LESEN.
    Auch ein Konto mit Scope 'EIGENE' (Monteur) darf beim Anlegen die Kategorie
    wählen; die Liste trägt keine objektgebundenen Daten, es gibt nichts zu
    begrenzen. Nur AKTIVe Kategorien, in Anzeigereihenfolge (sort_order).
    """
    require_scoped(request, "workflow", "LESEN")
    rows = ProjectCategory.objects.filter(status="AKTIV").order_by("sort_order", "name")
    return [
        CategoryOut(id=c.id, name=c.name, color_hex=c.color_hex) for c in rows
    ]


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


def _project_detail(project_id, *, eigene_objekte=None):
    """Projektdetail. `eigene_objekte` (Set von property_ids) begrenzt die Sicht.

    Ein Projekt kann über **mehrere** Liegenschaften laufen — und „meins" ist es
    schon, wenn EINE davon meine ist. Die übrigen dürfen darin nicht auftauchen:
    Sonst wäre die Projektakte das Schlupfloch, über das Name, Nummer und Ort eines
    fremden Objekts (und die Vorgänge daran) doch noch sichtbar würden. Bei Scope
    ALLE ist `eigene_objekte` None und es wird nichts gefiltert.
    """
    project = (
        Project.objects.filter(id=project_id)
        .select_related("category")
        .prefetch_related(
            "property_links__property__address",
            # Für die abgeleitete Kontaktkarte: Eigentümerrolle der Liegenschaft
            # samt Partei und deren Kontaktwegen. Ein Prefetch, kein N+1 pro Objekt.
            "property_links__property__party_roles__party__contact_points",
            "service_cases",
        )
        .first()
    )
    if project is None:
        raise HttpError(404, "Projekt nicht gefunden.")

    links = sorted(
        project.property_links.all(), key=lambda l: l.property.property_number
    )
    faelle = sorted(
        project.service_cases.all(), key=lambda c: c.received_at, reverse=True
    )
    if eigene_objekte is not None:
        links = [l for l in links if l.property_id in eigene_objekte]
        faelle = [c for c in faelle if c.property_id in eigene_objekte]

    properties = [
        PropertyRefOut(
            id=link.property.id,
            property_number=link.property.property_number,
            name=link.property.name,
            city=link.property.address.city,
        )
        for link in links
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
        for c in faelle
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
        primary_contact=_primary_contact(links),
    )


# Kontaktwege in Anzeigepriorität. Telefon vor Mobil, damit die Karte den
# Festnetz-/Hauptanschluss bevorzugt; E-Mail getrennt davon.
_PHONE_TYPES = ("PHONE", "MOBILE")
_EMAIL_TYPES = ("EMAIL",)


def _primary_contact(links):
    """Leitet den Hauptkontakt eines Projekts aus den (bereits scope-gefilterten,
    nach property_number sortierten) Liegenschafts-Links ab.

    Nimmt die erste Liegenschaft, die einen **aktuell gültigen** PROPERTY_OWNER
    trägt, und liefert Name + primäre E-Mail/Telefonnummer dieser Partei. Kein
    Eigentümer an irgendeiner Liegenschaft → None (die Karte entfällt dann).

    Läuft rein auf vorgeladenen Relationen (`links` stammt aus `_project_detail`
    mit passendem prefetch), erzeugt also keine zusätzlichen Queries.
    """
    today = date.today()

    def _is_current(valid_until):
        # daterange [) — obere Grenze exklusiv (wie in property.py).
        return valid_until is None or valid_until > today

    def _pick(contact_points, types):
        cands = [
            c
            for c in contact_points
            if c.contact_type in types and _is_current(c.valid_until)
        ]
        if not cands:
            return None
        # Primär zuerst; unter gleichrangigen der zuletzt gültige. Innerhalb der
        # Telefontypen greift zusätzlich die Reihenfolge in `types` (PHONE < MOBILE).
        cands.sort(
            key=lambda c: (c.is_primary, -types.index(c.contact_type), c.valid_from),
            reverse=True,
        )
        return cands[0].value

    for link in links:
        prop = link.property
        owners = [
            r
            for r in prop.party_roles.all()
            if r.role == "PROPERTY_OWNER" and _is_current(r.valid_until)
        ]
        if not owners:
            continue
        owner = max(owners, key=lambda r: r.valid_from)
        party = owner.party
        contact_points = list(party.contact_points.all())
        return ContactCardOut(
            party_id=party.id,
            display_name=party.display_name,
            property_id=prop.id,
            property_name=prop.name,
            role=owner.role,
            email=_pick(contact_points, _EMAIL_TYPES),
            phone=_pick(contact_points, _PHONE_TYPES),
        )
    return None


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
    """Detail eines Projekts inkl. Liegenschaften und Vorgängen.

    Scope 'EIGENE': fremdes Projekt → 404. Ein Projekt, das über **mehrere** Objekte
    läuft, zeigt nur die meinen (siehe `_project_detail`) — sonst wäre die
    Projektakte der Nebeneingang zum fremden Objekt.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    guard_projekt(scope, actor, project_id)
    return _project_detail(project_id, eigene_objekte=_eigene_objekte(scope, actor))


# --- Vorgangs-Board: Vorgänge über alle Projekte ---------------------------

class BoardColumnOut(Schema):
    status: str
    label: str
    sort_order: int
    is_final: bool
    # Endspalte (ABGESCHLOSSEN/ABGELEHNT): per Default ohne Karten geladen, aber
    # als Spalte/Drop-Ziel weiter sichtbar.
    is_terminal: bool


class ServiceCaseCardOut(Schema):
    id: UUID
    case_number: str
    subject: str
    status: str
    priority: str
    project_id: UUID | None = None
    project_name: str | None = None
    received_at: datetime


class ServiceCaseBoardOut(Schema):
    # Spalten aus workflow.status_catalog (Reihenfolge/Labels), damit das Board
    # ohne zweiten Request die Spalten kennt.
    columns: list[BoardColumnOut]
    items: list[ServiceCaseCardOut]
    total: int
    page: int
    page_size: int


class ServiceCaseBoardFilter(Schema):
    project_id: UUID | None = None
    status: str | None = None
    q: str | None = None
    # Endspalten-Karten mitladen (ABGESCHLOSSEN/ABGELEHNT). Default aus: das Board
    # zeigt die offenen Vorgänge. Ein expliziter status-Filter hat Vorrang.
    include_terminal: bool = False


def _service_case_card(case):
    return ServiceCaseCardOut(
        id=case.id,
        case_number=case.case_number,
        subject=case.subject,
        status=case.status,
        priority=case.priority,
        project_id=case.project_id,
        project_name=case.project.name if case.project_id else None,
        received_at=case.received_at,
    )


@router.get("/service_cases", response=ServiceCaseBoardOut)
def list_service_cases(
    request,
    filters: ServiceCaseBoardFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
):
    """Vorgänge über alle Projekte fürs Kanban-Board (Spalten = Statuskatalog).

    Scope 'EIGENE' (Objektsicht): Das Board zeigt die Vorgänge **meiner Objekte** —
    auch die der Kollegen. Genau darum geht es: „Zwei Tage vorher hat bei einem
    anderen Mieter der Heizkörper geleckt" ist ein Vorgang, den ein anderer angelegt
    hat. `service_case.property_id` ist NOT NULL, die Begrenzung deshalb ein direkter
    Filter (kein Coalesce nötig wie beim Einsatz).

    Filter: project_id, status (exakt), q (Freitext auf Nummer/Betreff). Ohne
    expliziten status-Filter werden Endspalten-Vorgänge (ABGESCHLOSSEN/ABGELEHNT)
    per Default ausgeblendet; include_terminal=true lädt sie mit. N+1-frei über
    select_related('project'); die Spalten kommen aus einem eigenen (zeilenzahl-
    unabhängigen) Katalog-Query.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    qs = ServiceCase.objects.select_related("project")
    qs = objektsicht.begrenzen(qs, scope, actor, "property_id")

    if filters.project_id:
        qs = qs.filter(project_id=filters.project_id)
    if filters.status:
        qs = qs.filter(status=filters.status)
    elif not filters.include_terminal:
        qs = qs.exclude(status__in=projekt_service.TERMINAL_SERVICE_CASE_STATUSES)
    if filters.q:
        needle = filters.q.strip()
        qs = qs.filter(
            Q(subject__icontains=needle) | Q(case_number__icontains=needle)
        )

    qs = qs.order_by("-received_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_service_case_card(c) for c in qs[start:start + page_size]]
    return ServiceCaseBoardOut(
        columns=[BoardColumnOut(**col) for col in projekt_service.service_case_board_columns()],
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


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
    """Logbuch-Einträge eines Projekts (neueste zuerst).

    **Scope 'EIGENE' → 403 (fail-closed). Bewusst, nicht vergessen.**

    Der Rest dieses Slices begrenzt Projektinhalte auf **meine Objekte** — der
    Projekt-Detail filtert seine Liegenschaften und Vorgänge, das Dossier ebenso. Das
    Logbuch lässt sich so **nicht** begrenzen: Ein Eintrag ist **Freitext ohne
    Objektbezug**. Ein Projekt gilt schon als „meins", wenn EINE seiner
    Liegenschaften meine ist — der Eintrag „Abstimmung mit der Verwaltung wegen
    Badensche 53, Mieter dort will Termin verschieben" nennt Objekt B beim Namen, und
    keine Spalte der Welt sagt mir das vorher.

    Es ist die einzige Stelle im Slice, an der Projektinhalte ohne Objektbezug an die
    Objektsicht gingen. Ein stilles Durchreichen wäre ein Leak; eine leere Liste wäre
    eine Lüge („es gibt nichts" statt „du darfst es nicht"). Also 403 mit Grund.

    **Fachlich ist das auch das Richtige:** Das Logbuch ist Bürokommunikation
    (Abstimmungen, Telefonate, Entscheidungen), kein Baustellenwissen. Was der Monteur
    braucht — Objekthistorie, Berichte der Kollegen, Wartungslage — bekommt er über
    das Liegenschafts-Dossier, und zwar objektgenau.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    verbiete_eigene(
        scope,
        "Ihre Rolle erlaubt nur den Zugriff auf eigene Objekte; das Projektlogbuch "
        "ist Freitext ohne Objektbezug und lässt sich darauf nicht begrenzen.",
    )
    guard_projekt(scope, actor, project_id)
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
    """Checklisten eines Projekts inkl. Punkten (erledigt-Status).

    **Scope 'EIGENE' → 403 (fail-closed)** — dieselbe Begründung wie beim Logbuch:
    Ein Checklistenpunkt (`label`) ist **Freitext ohne Objektbezug** („Zählerstand
    Badensche 53 ablesen"). Ein Projekt über mehrere Objekte reicht damit Inhalte zu
    fremden Objekten durch, und keine Spalte erlaubt eine belastbare Begrenzung.

    Die Checkliste ist Projektsteuerung, nicht Baustellenwissen. Was der Monteur
    abarbeiten soll, steht in **seiner Aufgabe** (`workflow.task`, eigene Zuweisung)
    und in **seinem Einsatz** — beides sieht er.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    verbiete_eigene(
        scope,
        "Ihre Rolle erlaubt nur den Zugriff auf eigene Objekte; Projektchecklisten "
        "sind Freitext ohne Objektbezug und lassen sich darauf nicht begrenzen.",
    )
    guard_projekt(scope, actor, project_id)
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


def _service_case_detail(case_id):
    """Baut das Vorgangsdetail (Liegenschaft, Projekt, Melder, Statusverlauf)
    oder wirft 404. Von Lese- und Schreibendpunkten geteilt."""
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


@router.get("/service_cases/{case_id}", response=ServiceCaseDetailOut)
def get_service_case(request, case_id: UUID):
    """Detail eines Vorgangs inkl. Liegenschaft, Projekt, Melder und
    Statusverlauf (append-only aus workflow.status_change).

    Scope 'EIGENE': Vorgang an einem fremden Objekt → 404.
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    _guard_vorgang(case_id, actor, scope)
    return _service_case_detail(case_id)


# --- Vorgang: Statuswechsel ------------------------------------------------

class ServiceCaseTransitionOut(Schema):
    to_status: str
    label: str
    reason_required: bool
    # Modulrecht, das dieser Übergang verlangt: FREIGEBEN für die Beauftragung
    # (FREIGABE_AUSSTEHEND → BEAUFTRAGT), sonst AENDERN. Das Frontend blendet den
    # Knopf nur bei darf('workflow', recht) ein.
    recht: str


class ServiceCaseStatusIn(Schema):
    to_status: str
    reason: str | None = None


@router.get(
    "/service_cases/{case_id}/transitions", response=list[ServiceCaseTransitionOut]
)
def get_service_case_transitions(request, case_id: UUID):
    """Erlaubte nächste Status eines Vorgangs — zur Laufzeit aus
    workflow.status_transition gelesen (seit 0042 konfigurierbar), Labels aus
    workflow.status_catalog. Read-only (Recht workflow.LESEN).

    Scope 'EIGENE': fremder Vorgang → 404. Dass die Liste **möglicher** Übergänge
    sichtbar ist, heißt nicht, dass der Akteur sie ausführen darf — der Statuswechsel
    selbst bleibt für 'EIGENE' fail-closed (403).
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    case = ServiceCase.objects.filter(id=case_id).only("id", "status").first()
    if case is None:
        raise HttpError(404, "Vorgang nicht gefunden.")
    _guard_vorgang(case_id, actor, scope)
    return projekt_service.service_case_transitions(case.status)


@router.post(
    "/service_cases/{case_id}/status",
    response=ServiceCaseDetailOut,
    auth=django_auth,
)
def advance_service_case_status(request, case_id: UUID, payload: ServiceCaseStatusIn):
    """Statuswechsel eines Vorgangs durchführen (gegen workflow.status_transition
    validiert; der DB-Trigger ist die maßgebliche Instanz).

    Recht: Der Übergang FREIGABE_AUSSTEHEND → BEAUFTRAGT ist die eigentliche
    Beauftragung/Freigabe (Freigabetor) und verlangt workflow.FREIGEBEN; alle
    übrigen Wechsel workflow.AENDERN. So kann ein Konto mit AENDERN, aber ohne
    FREIGEBEN, den Vorgang nicht beauftragen.

    Torfunktion `require` (fail-closed), nicht `require_scoped`: der Vorgang
    hängt an einem Projekt, das der Akteur womöglich nicht sehen darf, und diese
    Ansicht wertet den row_scope nicht aus. Ein Konto mit Scope EIGENE (Monteur)
    erhält daher 403 — analog zum Anlegen (create_service_case), das genau
    dieses Rechte-Loch geschlossen hat. Unbekannter Vorgang → 404 (vor dem
    Service geprüft, damit er nicht als 422 durchschlägt)."""
    action = projekt_service.service_case_status_recht(payload.to_status)
    actor, _ = require(request, "workflow", action)
    if not ServiceCase.objects.filter(id=case_id).exists():
        raise HttpError(404, "Vorgang nicht gefunden.")
    try:
        projekt_service.advance_service_case_status(
            actor,
            service_case_id=case_id,
            to_status=payload.to_status,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _service_case_detail(case_id)


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

    Torfunktion `require` (fail-closed), nicht `require_create`: Die Checkliste
    hat zwar kein setzbares Owner-Feld, hängt aber an einem **fremden Projekt**.
    Ein Konto mit Scope EIGENE könnte sonst Zeilen an Projekten erzeugen, die es
    nicht einmal lesen darf. `require_create` schützt nur vor fremder Zuweisung,
    nicht vor dem Anlegen außerhalb des eigenen Sichtfelds.
    """
    actor, _ = require(request, "workflow", "ANLEGEN")
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

    Torfunktion `require` (fail-closed), nicht `require_create`: Der Vorgang
    trägt zwar kein setzbares Owner-Feld, hängt aber an einem **fremden Projekt**
    und verbraucht eine Belegnummer (V-JJJJ-NNNNNN, GoBD-Sequenz). Ein Konto mit
    Scope EIGENE könnte sonst nummerierte Vorgänge quer über alle Projekte
    erzeugen, die es anschließend nicht lesen darf. `require_create` schützt nur
    vor fremder Zuweisung, nicht vor dem Anlegen außerhalb des Sichtfelds.
    """
    actor, _ = require(request, "workflow", "ANLEGEN")
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


# --- Schnellerfassung / Vorgang ohne Projekt (Session-Auth Pflicht) --------

@router.post("/service_cases", response={201: ServiceCaseOut}, auth=django_auth)
def create_service_case_standalone(request, payload: ServiceCaseIn):
    """Vorgang (service_case) OHNE Projekt anlegen (Initialstatus NEU).

    Das Projekt ist eine optionale Klammer (B-09); kleine Meldungen — der
    Regelfall beim Einfamilienhaus — brauchen keins. `service_case.project_id`
    ist NULL-fähig; der Service lässt project_id auf None. Die Liegenschaft
    (`property_id`) bleibt Pflicht.

    Torfunktion `require` (fail-closed), nicht `require_create`: Der Vorgang
    verbraucht eine GoBD-Belegnummer (V-JJJJ-NNNNNN). Ein Konto mit Scope EIGENE
    bekommt hier fail-closed 403, analog zum projektgebundenen Endpunkt.
    """
    actor, _ = require(request, "workflow", "ANLEGEN")
    try:
        case = projekt_service.create_service_case(
            actor,
            property_id=payload.property_id,
            subject=payload.subject,
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


class QuickIntakePersonIn(Schema):
    # Dedup: Ist der Anrufer schon als Kontakt erfasst, wird er hier referenziert
    # und NICHT neu angelegt. Dann sind Vor-/Nachname unnötig; ohne
    # existing_party_id bleiben sie Pflicht (im Endpunkt geprüft).
    existing_party_id: UUID | None = None
    salutation: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class QuickIntakeContactIn(Schema):
    phone: str | None = None
    email: str | None = None


class QuickIntakePropertyIn(Schema):
    # Dedup: Ist die Liegenschaft schon im System, wird sie hier referenziert und
    # NICHT neu angelegt (kein Duplikat, keine zweite Adresse). Dann sind die
    # Adressfelder unnötig; ohne existing_property_id bleiben sie Pflicht (im
    # Endpunkt geprüft).
    existing_property_id: UUID | None = None
    property_type: str = "EINFAMILIENHAUS"
    name: str | None = None
    street: str | None = None
    house_number: str | None = None
    postal_code: str | None = None
    city: str | None = None


class QuickIntakeMeldungIn(Schema):
    subject: str
    description: str | None = None
    priority: str = "NORMAL"


class QuickIntakeIn(Schema):
    person: QuickIntakePersonIn
    contact: QuickIntakeContactIn | None = None
    property: QuickIntakePropertyIn
    meldung: QuickIntakeMeldungIn


class QuickIntakeOut(Schema):
    party_id: UUID
    property_id: UUID
    service_case: ServiceCaseOut


def _ableiten_liegenschaftsname(prop: QuickIntakePropertyIn) -> str:
    """property.name ist Pflicht; beim EFH fragen wir ihn nicht ab und leiten ihn
    aus der Adresse ab (Straße Hausnr, Ort)."""
    if prop.name and prop.name.strip():
        return prop.name.strip()
    strasse = " ".join(
        teil.strip()
        for teil in (prop.street, prop.house_number)
        if teil and teil.strip()
    )
    stadt = prop.city.strip() if prop.city else ""
    name = ", ".join(teil for teil in (strasse, stadt) if teil)
    return name or (prop.street or "").strip()


@router.post("/quick-intake", response={201: QuickIntakeOut}, auth=django_auth)
def quick_intake(request, payload: QuickIntakeIn):
    """Schnellerfassung: Person + Liegenschaft + Vorgang (ohne Projekt) atomar.

    Der Alltagsfall „Kunde ruft an, Defekt xy" in einem Schritt: Kontakt anlegen,
    optional den Rückruf-Kontaktweg (Telefon/E-Mail), die Liegenschaft mit
    Eigentümer-Rolle (PROPERTY_OWNER) und der Vorgang (Initialstatus NEU, kein
    Projekt). Alles läuft in EINER Transaktion (`run_business_transaction`); die
    service-internen `business_transaction`-Aufrufe werden zu Savepoints. Schlägt
    ein Teilschritt fehl, rollt der gesamte Durchstich zurück — es bleiben keine
    Waisen (Person/Liegenschaft ohne Vorgang), die der No-Delete-Schutz nicht mehr
    entfernen könnte.

    Fail-closed Tore, VOR der Transaktion geprüft. Sie richten sich nach dem, was
    tatsächlich geschrieben wird (least privilege): identity.ANLEGEN für einen
    neuen Kontakt bzw. nur identity.LESEN, wenn ein bestehender referenziert wird
    (existing_party_id); property.ANLEGEN+AENDERN für eine neue Liegenschaft bzw.
    nur property.LESEN bei Referenz (existing_property_id); immer workflow.ANLEGEN
    (der Vorgang verbraucht eine GoBD-Belegnummer). Dedup verhindert Duplikate von
    Person/Liegenschaft. Bei Referenz einer BESTEHENDEN Liegenschaft wird der
    Melder nicht als Eigentümer eingetragen (er ist nur Melder); bei einer NEUEN
    Liegenschaft wird der Melder — ob neu oder referenziert — deren Eigentümer.
    """
    existing_party_id = payload.person.existing_party_id
    if existing_party_id is None:
        # Neuer Kontakt: identity-Schreibrecht nötig, Name Pflicht.
        actor, _ = require(request, "identity", "ANLEGEN")
        if not (
            payload.person.first_name
            and payload.person.first_name.strip()
            and payload.person.last_name
            and payload.person.last_name.strip()
        ):
            raise HttpError(422, "Für einen neuen Kontakt sind Vor- und Nachname Pflicht.")
    else:
        # Bestehenden Kontakt nur als Melder referenzieren → Lese-Recht genügt.
        actor, _ = require(request, "identity", "LESEN")
    require(request, "workflow", "ANLEGEN")

    existing_property_id = payload.property.existing_property_id
    if existing_property_id is None:
        # Neue Liegenschaft: property-Schreibrechte nötig, Adresse Pflicht.
        require(request, "property", "ANLEGEN")
        require(request, "property", "AENDERN")
        if not (
            payload.property.street
            and payload.property.street.strip()
            and payload.property.postal_code
            and payload.property.postal_code.strip()
            and payload.property.city
            and payload.property.city.strip()
        ):
            raise HttpError(
                422,
                "Für eine neue Liegenschaft sind Straße, PLZ und Ort Pflicht.",
            )
        prop_name = _ableiten_liegenschaftsname(payload.property)
    else:
        # Bestehende Liegenschaft nur referenzieren → Lese-Recht genügt; der
        # Anrufer wird NICHT als Eigentümer eingetragen (er ist Melder, nicht
        # zwingend Eigentümer eines bereits erfassten Objekts).
        require(request, "property", "LESEN")

    def _durchstich():
        if existing_party_id is None:
            party = identity_service.create_person(
                actor,
                payload.person.first_name,
                payload.person.last_name,
                salutation=payload.person.salutation,
            )
            if payload.contact:
                if payload.contact.phone and payload.contact.phone.strip():
                    identity_service.add_contact_point(
                        actor,
                        party.id,
                        contact_type="PHONE",
                        value=payload.contact.phone,
                        is_primary=True,
                    )
                if payload.contact.email and payload.contact.email.strip():
                    identity_service.add_contact_point(
                        actor,
                        party.id,
                        contact_type="EMAIL",
                        value=payload.contact.email,
                        is_primary=True,
                    )
            party_id = party.id
        else:
            # Bestehender Melder: Existenz/Verwendbarkeit prüft
            # create_service_case (ensure_party_usable → 422). Keine neuen
            # Kontaktwege am fremden Kontakt.
            party_id = existing_party_id
        if existing_property_id is None:
            prop = property_service.create_property(
                actor,
                name=prop_name,
                property_type=payload.property.property_type,
                street=payload.property.street,
                house_number=payload.property.house_number,
                postal_code=payload.property.postal_code,
                city=payload.property.city,
            )
            property_service.add_party_role(
                actor,
                property_id=prop.id,
                party_id=party_id,
                role="PROPERTY_OWNER",
                valid_from=date.today(),
            )
            property_id = prop.id
        else:
            # Existenz prüft create_service_case selbst (ensure_exists → 422).
            property_id = existing_property_id
        case = projekt_service.create_service_case(
            actor,
            property_id=property_id,
            subject=payload.meldung.subject,
            project_id=None,
            description=payload.meldung.description,
            reported_by_party_id=party_id,
            priority=payload.meldung.priority,
        )
        return party_id, property_id, case

    try:
        party_id, property_id, case = run_business_transaction(actor, _durchstich)
    except ValueError as exc:
        raise HttpError(422, str(exc))

    return Status(
        201,
        QuickIntakeOut(
            party_id=party_id,
            property_id=property_id,
            service_case=ServiceCaseOut(
                id=case.id,
                case_number=case.case_number,
                subject=case.subject,
                status=case.status,
                priority=case.priority,
                received_at=case.received_at,
            ),
        ),
    )


# --- Vorgang zum Projekt hochstufen ----------------------------------------

class PromoteToProjectIn(Schema):
    # Optionaler Projektname; ohne Angabe wird der Vorgangsbetreff übernommen.
    name: str | None = None


@router.post(
    "/service_cases/{case_id}/promote-to-project",
    response={201: ProjectDetailOut},
    auth=django_auth,
)
def promote_service_case_to_project(request, case_id: UUID, payload: PromoteToProjectIn):
    """Vorgang zum Projekt hochstufen: ein neues Projekt anlegen und den Vorgang
    samt seiner Aufträge darunter hängen (die optionale Klammer nachträglich).

    Nur zulässig, solange der Vorgang noch kein Projekt hat (sonst 422). Der Vorgang
    (404 bei unbekannter id) und alle projektlosen Aufträge werden umgehängt; das
    Projekt umfasst die Liegenschaften des Vorgangs und der Aufträge.

    Zwei fail-closed Tore: `ANLEGEN` (es entsteht ein neues Projekt mit
    GoBD-Belegnummer P-JJJJ-NNNNNN) UND `AENDERN` (bestehende Vorgänge/Aufträge
    werden umgehängt — dieselbe Änderungsschwelle wie beim Statuswechsel).
    """
    actor, _ = require(request, "workflow", "ANLEGEN")
    require(request, "workflow", "AENDERN")
    if not ServiceCase.objects.filter(id=case_id).exists():
        raise HttpError(404, "Vorgang nicht gefunden.")
    try:
        project = projekt_service.promote_service_case_to_project(
            actor, service_case_id=case_id, name=payload.name
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _project_detail(project.id))
