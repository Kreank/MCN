"""Aufgaben-Service: Aufgaben anlegen und ihren Status ändern.

Alle Writes über business_transaction (Audit). „Löschen" gibt es nicht — eine
Aufgabe wird erledigt (ERLEDIGT) oder verworfen (VERWORFEN); der DB-Trigger
verbietet physisches DELETE. Erledigen setzt completed_by/completed_at
(DB-CHECK erzwingt die Konsistenz zum Status).
"""
import uuid

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import AppUser, Project, Task, WorkOrder
from db_core.services._validation import ensure_exists, ensure_party_usable


def create_task(
    actor_app_user_id,
    *,
    title,
    description=None,
    due_date=None,
    assigned_to_user_id=None,
    project_id=None,
    party_id=None,
    work_order_id=None,
):
    """Legt eine Aufgabe im Status OFFEN an (created_by = Akteur).

    Die drei Bezuege sind **kombinierbar** (Befund D2): Eine Aufgabe am Auftrag
    haengt fast immer auch am Kunden, den man deswegen anruft. Die DB erzwingt
    deshalb bewusst keine Exklusivitaet.
    """
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
    ensure_exists(AppUser, assigned_to_user_id, "Benutzer")
    ensure_exists(Project, project_id, "Projekt")
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    ensure_party_usable(party_id, "Kontakt")
    with business_transaction(actor_app_user_id):
        task = Task.objects.create(
            id=uuid.uuid4(),
            title=title.strip(),
            description=description,
            due_date=due_date,
            status="OFFEN",
            assigned_to_id=assigned_to_user_id,
            project_id=project_id,
            party_id=party_id,
            work_order_id=work_order_id,
            created_by_id=actor_app_user_id,
            version=1,
        )
    return task


#: Sentinel für „Feld nicht übergeben" — trennt „nicht gesetzt" von „auf None
#: gesetzt" (Löschen einer optionalen Zuordnung). Die API reicht nur die
#: tatsächlich übergebenen Felder durch (exclude_unset).
_UNSET = object()


def update_task(
    actor_app_user_id,
    task_id,
    *,
    title=_UNSET,
    description=_UNSET,
    due_date=_UNSET,
    assigned_to_user_id=_UNSET,
    project_id=_UNSET,
    party_id=_UNSET,
    work_order_id=_UNSET,
):
    """Ändert die inhaltlichen Felder einer Aufgabe — nur die übergebenen.

    Ausdrücklich KEIN Statuswechsel: Erledigen/Verwerfen/Wiederöffnen laufen über
    die eigenen Funktionen (mit ihrer completed_by/at-Konsistenz). Ein nicht
    übergebenes Feld (`_UNSET`) bleibt unverändert; `None` löscht eine optionale
    Zuordnung. Unbekannte Fremdschlüssel → ValueError (die API übersetzt in 422).
    """
    if title is not _UNSET and (not title or not title.strip()):
        raise ValueError("title darf nicht leer sein.")
    if assigned_to_user_id is not _UNSET:
        ensure_exists(AppUser, assigned_to_user_id, "Benutzer")
    if project_id is not _UNSET:
        ensure_exists(Project, project_id, "Projekt")
    if party_id is not _UNSET:
        ensure_party_usable(party_id, "Kontakt")
    if work_order_id is not _UNSET:
        ensure_exists(WorkOrder, work_order_id, "Auftrag")

    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        update_fields = []
        if title is not _UNSET:
            task.title = title.strip()
            update_fields.append("title")
        if description is not _UNSET:
            task.description = description
            update_fields.append("description")
        if due_date is not _UNSET:
            task.due_date = due_date
            update_fields.append("due_date")
        if assigned_to_user_id is not _UNSET:
            task.assigned_to_id = assigned_to_user_id
            update_fields.append("assigned_to")
        if project_id is not _UNSET:
            task.project_id = project_id
            update_fields.append("project")
        if party_id is not _UNSET:
            task.party_id = party_id
            update_fields.append("party")
        if work_order_id is not _UNSET:
            task.work_order_id = work_order_id
            update_fields.append("work_order")
        if update_fields:
            task.save(update_fields=update_fields)
    return task


def _load(task_id):
    task = Task.objects.filter(id=task_id).first()
    if task is None:
        raise ValueError("Aufgabe nicht gefunden.")
    return task


def complete_task(actor_app_user_id, task_id):
    """Markiert eine Aufgabe als erledigt (setzt completed_by/at).

    Idempotent: ist die Aufgabe bereits erledigt, bleibt completed_by/at
    (der ursprüngliche Erlediger/Zeitpunkt) unverändert erhalten.
    """
    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        if task.status == "ERLEDIGT":
            return task
        task.status = "ERLEDIGT"
        task.completed_by_id = actor_app_user_id
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "completed_by", "completed_at"])
    return task


def discard_task(actor_app_user_id, task_id):
    """Verwirft eine Aufgabe (Status VERWORFEN statt Löschen). Idempotent."""
    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        if task.status == "VERWORFEN":
            return task
        task.status = "VERWORFEN"
        task.completed_by = None
        task.completed_at = None
        task.save(update_fields=["status", "completed_by", "completed_at"])
    return task


def reopen_task(actor_app_user_id, task_id):
    """Öffnet eine erledigte/verworfene Aufgabe wieder (Status OFFEN). Idempotent."""
    with business_transaction(actor_app_user_id):
        task = _load(task_id)
        if task.status == "OFFEN":
            return task
        task.status = "OFFEN"
        task.completed_by = None
        task.completed_at = None
        task.save(update_fields=["status", "completed_by", "completed_at"])
    return task
