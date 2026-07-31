"""Aufgaben-API — Aufgaben (workflow.task).

Lesen in der Dev-Phase ohne Auth; Anlegen und Statusaktionen verlangen
Django-Session + zugeordnetes app_user. Views dünn, rufen die Service-Schicht.
"""
from datetime import date, datetime
from uuid import UUID

from django.db.models import Case, IntegerField, Q, Value, When
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from ninja.security import django_auth

from api.permissions import require_scoped
from db_core.models import Task, TaskComment
from db_core.services import aufgabe as aufgabe_service

router = Router()


# --- Schemas ---------------------------------------------------------------

class UserRefOut(Schema):
    id: UUID
    display_name: str


class ProjectRefOut(Schema):
    id: UUID
    project_number: str
    name: str


class PartyRefOut(Schema):
    id: UUID
    display_name: str


class WorkOrderRefOut(Schema):
    id: UUID
    order_number: str
    title: str


class TaskOut(Schema):
    id: UUID
    title: str
    description: str | None = None
    due_date: date | None = None
    status: str
    completed_at: datetime | None = None
    created_at: datetime
    assigned_to: UserRefOut | None = None
    # Der Ersteller war bisher nirgends sichtbar — dabei ist er derjenige, an
    # den sich die Rückfrage richtet und der die Erledigung erfahren muss.
    created_by: UserRefOut | None = None
    completed_by: UserRefOut | None = None
    project: ProjectRefOut | None = None
    party: PartyRefOut | None = None
    # Auftragsbezug (Befund D2). Kombinierbar mit Projekt und Kontakt — eine
    # Aufgabe am Auftrag haengt fast immer auch am Kunden.
    work_order: WorkOrderRefOut | None = None


class TaskListOut(Schema):
    items: list[TaskOut]
    total: int
    page: int
    page_size: int


class TaskIn(Schema):
    title: str
    description: str | None = None
    due_date: date | None = None
    assigned_to_user_id: UUID | None = None
    project_id: UUID | None = None
    party_id: UUID | None = None
    work_order_id: UUID | None = None


class TaskUpdate(Schema):
    """Bearbeiten: nur die tatsächlich gesendeten Felder werden geändert
    (`exclude_unset`). Statuswechsel läuft NICHT hierüber."""
    title: str | None = None
    description: str | None = None
    due_date: date | None = None
    assigned_to_user_id: UUID | None = None
    project_id: UUID | None = None
    party_id: UUID | None = None
    work_order_id: UUID | None = None


class TaskFilter(Schema):
    q: str | None = None
    status: str | None = None
    assigned_to_user_id: UUID | None = None
    project_id: UUID | None = None
    party_id: UUID | None = None
    work_order_id: UUID | None = None


def _user_ref(user, user_id):
    if not user_id:
        return None
    return UserRefOut(id=user.id, display_name=user.display_name)


def _task_out(task):
    assigned = _user_ref(task.assigned_to, task.assigned_to_id)
    project = (
        ProjectRefOut(
            id=task.project.id,
            project_number=task.project.project_number,
            name=task.project.name,
        )
        if task.project_id
        else None
    )
    party = (
        PartyRefOut(id=task.party.id, display_name=task.party.display_name)
        if task.party_id
        else None
    )
    auftrag = (
        WorkOrderRefOut(
            id=task.work_order.id,
            order_number=task.work_order.order_number,
            title=task.work_order.title,
        )
        if task.work_order_id
        else None
    )
    return TaskOut(
        id=task.id,
        title=task.title,
        description=task.description,
        due_date=task.due_date,
        status=task.status,
        completed_at=task.completed_at,
        created_at=task.created_at,
        assigned_to=assigned,
        created_by=_user_ref(task.created_by, task.created_by_id),
        completed_by=_user_ref(task.completed_by, task.completed_by_id),
        project=project,
        party=party,
        work_order=auftrag,
    )


#: Beide Beteiligten-Spalten werden jetzt in jeder Antwort ausgegeben — ohne
#: `select_related` kostete jede Zeile der Liste zwei Extra-Abfragen.
_TASK_RELATIONS = (
    "assigned_to",
    "created_by",
    "completed_by",
    "project",
    "party",
    "work_order",
)


# --- Lesende Endpoints -----------------------------------------------------

@router.get("/tasks", response=TaskListOut)
def list_tasks(
    request,
    filters: TaskFilter = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Aufgaben auflisten: Suche (Titel), Status-/Zuständigen-/Projekt-/Party-Filter.

    Ohne expliziten Statusfilter werden verworfene Aufgaben (VERWORFEN)
    ausgeblendet; gezielt abrufbar über status=VERWORFEN.

    Zeilenbegrenzung: Wer nur 'EIGENE' sehen darf (Monteur), bekommt die
    Aufgaben, an denen er hängt — die ihm zugewiesenen **und** die von ihm
    selbst gestellten (dieselbe Definition wie in `_guard_own_task`; sie muss
    mit ihr übereinstimmen, sonst zeigt die Liste etwas, das das Detail
    verweigert, oder umgekehrt).
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    qs = Task.objects.select_related(*_TASK_RELATIONS)
    if scope == "EIGENE":
        qs = qs.filter(Q(assigned_to_id=actor) | Q(created_by_id=actor))

    if filters.q:
        qs = qs.filter(title__icontains=filters.q.strip())
    if filters.status:
        qs = qs.filter(status=filters.status)
    else:
        qs = qs.exclude(status="VERWORFEN")
    if filters.assigned_to_user_id:
        qs = qs.filter(assigned_to_id=filters.assigned_to_user_id)
    if filters.project_id:
        qs = qs.filter(project_id=filters.project_id)
    if filters.party_id:
        qs = qs.filter(party_id=filters.party_id)
    if filters.work_order_id:
        qs = qs.filter(work_order_id=filters.work_order_id)

    # Offene zuerst, dann erledigt, dann verworfen; innerhalb nach Fälligkeit
    # (NULLs zuletzt), dann neueste. Alphabetische Sortierung würde ERLEDIGT
    # vor OFFEN stellen — daher expliziter Rang.
    qs = qs.annotate(
        status_rank=Case(
            When(status="OFFEN", then=Value(0)),
            When(status="ERLEDIGT", then=Value(1)),
            When(status="VERWORFEN", then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by("status_rank", "due_date", "-created_at", "id")

    total = qs.count()
    start = (page - 1) * page_size
    items = [_task_out(t) for t in qs[start:start + page_size]]
    return TaskListOut(items=items, total=total, page=page, page_size=page_size)


# --- Schreibende Endpoints (Session-Auth Pflicht) --------------------------

def _guard_own_task(task_id, actor, scope):
    """Bei Scope 'EIGENE': nur die EIGENE Aufgabe ist zugänglich.

    „Eigen" heißt **zugewiesen ODER selbst erstellt**. Bis Migration 0137 zählte
    nur die Zuweisung — das war zu eng, und mit den Benachrichtigungen wurde es
    zu einem Leck: Der Ersteller bekommt jede Meldung zu seiner Aufgabe (samt
    Auszug aus einer Rückfrage), lief beim Anklicken aber in 404, sobald die
    Aufgabe jemand anderem zugewiesen war. Entweder darf er sie sehen, oder er
    darf nichts über sie erfahren; das erste ist das fachlich Richtige — er hat
    sie schließlich gestellt und wartet auf ihre Erledigung.

    Fremde (oder nicht existierende) Aufgabe → 404, nicht 403: die Existenz
    fremder Zeilen soll nicht verraten werden. Bei 'ALLE' passiert nichts; ein
    fehlender Datensatz fällt dann im Service auf.
    """
    if scope != "EIGENE":
        return
    beteiligt = Task.objects.filter(
        Q(id=task_id) & (Q(assigned_to_id=actor) | Q(created_by_id=actor))
    ).exists()
    if not beteiligt:
        raise HttpError(404, "Aufgabe nicht gefunden.")


def _reload(task_id):
    task = Task.objects.select_related(*_TASK_RELATIONS).filter(id=task_id).first()
    if task is None:
        raise HttpError(404, "Aufgabe nicht gefunden.")
    return _task_out(task)


@router.get("/tasks/{task_id}", response=TaskOut)
def get_task(request, task_id: UUID):
    """Eine Aufgabe einzeln — die Grundlage der Detailseite `/aufgaben/{id}`.

    Bis hierhin gab es nur die Liste; eine Benachrichtigung hätte also kein
    Ziel gehabt, das man anspringen kann.

    Zeilenbegrenzung wie überall in dieser Datei: Bei Scope EIGENE ist nur die
    eigene Aufgabe zugänglich — zugewiesen ODER selbst erstellt, siehe
    `_guard_own_task` —, alles andere ist 404 (die Existenz fremder Zeilen wird
    nicht verraten).
    """
    actor, scope = require_scoped(request, "workflow", "LESEN")
    _guard_own_task(task_id, actor, scope)
    return _reload(task_id)


@router.post("/tasks", response={201: TaskOut}, auth=django_auth)
def create_task(request, payload: TaskIn):
    """Neue Aufgabe anlegen (Status OFFEN).

    `require_scoped` statt `require_create`: die Aufgabe trägt mit `assigned_to`
    ein Owner-Feld, das der Erzeuger frei setzen kann. Wer nur eigene Zeilen
    sehen darf, könnte sonst Aufgaben auf fremde Listen legen — und sie danach
    selbst nicht mehr sehen. Bei Scope EIGENE wird eine fremde Zuweisung deshalb
    abgelehnt und die leere Zuweisung auf den Akteur gesetzt.
    """
    actor, scope = require_scoped(request, "workflow", "ANLEGEN")
    assigned_to = payload.assigned_to_user_id
    if scope == "EIGENE":
        if assigned_to not in (None, actor):
            raise HttpError(
                403,
                "Ihre Rolle erlaubt nur eigene Datensätze; eine Aufgabe kann "
                "nicht einer anderen Person zugewiesen werden.",
            )
        assigned_to = actor
    try:
        task = aufgabe_service.create_task(
            actor,
            title=payload.title,
            description=payload.description,
            due_date=payload.due_date,
            assigned_to_user_id=assigned_to,
            project_id=payload.project_id,
            party_id=payload.party_id,
            work_order_id=payload.work_order_id,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return Status(201, _reload(task.id))


@router.patch("/tasks/{task_id}", response=TaskOut, auth=django_auth)
def update_task(request, task_id: UUID, payload: TaskUpdate):
    """Aufgabe bearbeiten (Titel/Beschreibung/Fälligkeit/Zuweisung/Projekt/Kontakt).

    Rechte exakt wie `create_task`: `require_scoped` auf workflow/AENDERN, und bei
    Scope EIGENE …
      * … ist nur die eigene Aufgabe zugänglich — zugewiesen ODER selbst erstellt
        (`_guard_own_task` → fremde/nicht existierende Aufgabe = 404, verrät die
        Existenz nicht);
      * … darf die Aufgabe nicht an eine andere Person umgehängt werden — eine
        fremde Zuweisung wird abgelehnt (403), eine leere Zuweisung fällt auf den
        Akteur zurück (er würde sich sonst selbst aus dem Sichtfeld schreiben).

    Nur gesendete Felder werden geändert (`exclude_unset`). Statuswechsel bleibt
    den eigenen Endpunkten vorbehalten. Unbekannte Fremdschlüssel → 422.
    """
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    _guard_own_task(task_id, actor, scope)
    if scope != "EIGENE" and not Task.objects.filter(id=task_id).exists():
        # Bei EIGENE hat _guard_own_task schon 404 geworfen; bei ALLE fehlt die
        # Existenzprüfung, sonst würde ein fehlender Satz als 422 durchschlagen.
        raise HttpError(404, "Aufgabe nicht gefunden.")

    data = payload.dict(exclude_unset=True)
    if scope == "EIGENE" and "assigned_to_user_id" in data:
        assigned = data["assigned_to_user_id"]
        if assigned not in (None, actor):
            raise HttpError(
                403,
                "Ihre Rolle erlaubt nur eigene Datensätze; eine Aufgabe kann "
                "nicht einer anderen Person zugewiesen werden.",
            )
        data["assigned_to_user_id"] = actor

    try:
        aufgabe_service.update_task(actor, task_id, **data)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    return _reload(task_id)


@router.post("/tasks/{task_id}/complete", response=TaskOut, auth=django_auth)
def complete_task(request, task_id: UUID):
    """Aufgabe als erledigt markieren."""
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    _guard_own_task(task_id, actor, scope)
    try:
        aufgabe_service.complete_task(actor, task_id)
    except ValueError as exc:
        raise HttpError(404, str(exc))
    return _reload(task_id)


@router.post("/tasks/{task_id}/discard", response=TaskOut, auth=django_auth)
def discard_task(request, task_id: UUID):
    """Aufgabe verwerfen (Status VERWORFEN statt Löschen)."""
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    _guard_own_task(task_id, actor, scope)
    try:
        aufgabe_service.discard_task(actor, task_id)
    except ValueError as exc:
        raise HttpError(404, str(exc))
    return _reload(task_id)


@router.post("/tasks/{task_id}/reopen", response=TaskOut, auth=django_auth)
def reopen_task(request, task_id: UUID):
    """Erledigte/verworfene Aufgabe wieder öffnen (Status OFFEN)."""
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    _guard_own_task(task_id, actor, scope)
    try:
        aufgabe_service.reopen_task(actor, task_id)
    except ValueError as exc:
        raise HttpError(404, str(exc))
    return _reload(task_id)


# --- Rückfragen (workflow.task_comment) ------------------------------------

class TaskCommentOut(Schema):
    id: UUID
    kind: str  # KOMMENTAR (Mensch) | SYSTEM (Statuswechsel-Vermerk)
    body: str
    created_at: datetime
    created_by: UserRefOut


class TaskCommentIn(Schema):
    body: str


def _comment_out(k):
    return TaskCommentOut(
        id=k.id,
        kind=k.kind,
        body=k.body,
        created_at=k.created_at,
        created_by=UserRefOut(
            id=k.created_by.id, display_name=k.created_by.display_name
        ),
    )


@router.get("/tasks/{task_id}/comments", response=list[TaskCommentOut])
def list_comments(request, task_id: UUID):
    """Der Faden einer Aufgabe (Rückfragen + Systemvermerke), älteste zuerst."""
    actor, scope = require_scoped(request, "workflow", "LESEN")
    _guard_own_task(task_id, actor, scope)
    if not Task.objects.filter(id=task_id).exists():
        raise HttpError(404, "Aufgabe nicht gefunden.")
    return [_comment_out(k) for k in aufgabe_service.kommentare(task_id)]


@router.post("/tasks/{task_id}/comments", response={201: TaskCommentOut}, auth=django_auth)
def create_comment(request, task_id: UUID, payload: TaskCommentIn):
    """Rückfrage oder Antwort in den Faden schreiben — append-only.

    Recht `workflow/AENDERN`, nicht `LESEN`: Der Eintrag landet dauerhaft und
    unlöschbar am Datensatz und geht als Benachrichtigung an die Gegenseite.
    Wer an Aufträgen und Aufgaben nur zuschauen darf, soll darin nicht schreiben
    können. Alle Rollen, die tatsächlich mit Aufgaben arbeiten (auch MONTEUR mit
    Scope EIGENE), tragen das Recht ohnehin — sonst könnten sie nicht erledigen.

    Zeilenbegrenzung wie am Statuswechsel: Bei Scope EIGENE ist nur die eigene
    Aufgabe zugänglich (fremde → 404).
    """
    actor, scope = require_scoped(request, "workflow", "AENDERN")
    _guard_own_task(task_id, actor, scope)
    try:
        kommentar = aufgabe_service.kommentieren(actor, task_id, payload.body)
    except ValueError as exc:
        # „Aufgabe nicht gefunden." kommt aus _load, alles andere ist Eingabe.
        if "nicht gefunden" in str(exc):
            raise HttpError(404, str(exc))
        raise HttpError(422, str(exc))
    kommentar = (
        TaskComment.objects.select_related("created_by").filter(id=kommentar.id).first()
    )
    return Status(201, _comment_out(kommentar))
