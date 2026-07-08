"""Aufgaben-Service: Aufgaben anlegen und ihren Status ändern.

Alle Writes über business_transaction (Audit). „Löschen" gibt es nicht — eine
Aufgabe wird erledigt (ERLEDIGT) oder verworfen (VERWORFEN); der DB-Trigger
verbietet physisches DELETE. Erledigen setzt completed_by/completed_at
(DB-CHECK erzwingt die Konsistenz zum Status).
"""
import uuid

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import Task


def create_task(
    actor_app_user_id,
    *,
    title,
    description=None,
    due_date=None,
    assigned_to_user_id=None,
    project_id=None,
    party_id=None,
):
    """Legt eine Aufgabe im Status OFFEN an (created_by = Akteur)."""
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
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
            created_by_id=actor_app_user_id,
            version=1,
        )
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
