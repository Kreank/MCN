"""Projekt-Service: Projekte, Projekt↔Liegenschaft, Vorgänge anlegen.

Wie die übrigen Services laufen alle Writes über business_transaction (setzt
app.current_user_id für Audit/Statusprotokoll). Projekt-Nummern (P-…) und
Vorgangsnummern (V-…) vergibt die DB über workflow.next_number; die Models
lassen die Spalten ungesetzt (db_default) und laden frisch nach.

Der Projekt-„Status" ist nur OPEN/CLOSED (kein Statusautomat). Der Vorgang
(service_case) hat dagegen einen Trigger-gestützten Statusautomaten; hier wird
nur der Initialzustand NEU angelegt — Statuswechsel folgen als eigener Slice.
"""
import uuid

from db_core.db_context import business_transaction
from db_core.models import Project, ProjectProperty, ServiceCase

PRIORITIES = ("NORMAL", "DRINGEND", "NOTFALL")


def create_project(
    actor_app_user_id,
    *,
    name,
    category_id=None,
    property_ids=None,
    start_date=None,
    target_end_date=None,
    responsible_user_id=None,
):
    """Legt ein workflow.project an und verknüpft optional Liegenschaften.

    Gibt das frisch nachgeladene Projekt zurück (mit vergebener Projektnummer).
    """
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")

    with business_transaction(actor_app_user_id):
        project = Project.objects.create(
            id=uuid.uuid4(),
            name=name.strip(),
            status="OPEN",
            start_date=start_date,
            target_end_date=target_end_date,
            responsible_user_id=responsible_user_id,
            category_id=category_id,
            version=1,
        )
        for property_id in property_ids or []:
            ProjectProperty.objects.create(
                project_id=project.id, property_id=property_id
            )
        project.refresh_from_db()
    return project


def create_service_case(
    actor_app_user_id,
    *,
    property_id,
    subject,
    project_id=None,
    description=None,
    reported_by_party_id=None,
    priority="NORMAL",
):
    """Legt einen workflow.service_case (Vorgang) im Initialstatus NEU an.

    property_id ist Pflicht (Liegenschaftsbezug). Der Trigger erzwingt NEU als
    Startstatus; responsibility_scope startet als UNKNOWN.
    """
    if not subject or not subject.strip():
        raise ValueError("subject darf nicht leer sein.")
    if priority not in PRIORITIES:
        raise ValueError(
            f"Ungültige priority '{priority}'. Erlaubt: {', '.join(PRIORITIES)}."
        )
    with business_transaction(actor_app_user_id):
        case = ServiceCase.objects.create(
            id=uuid.uuid4(),
            project_id=project_id,
            subject=subject.strip(),
            description=description,
            reported_by_party_id=reported_by_party_id,
            property_id=property_id,
            responsibility_scope="UNKNOWN",
            priority=priority,
            status="NEU",
            version=1,
        )
        case.refresh_from_db()
    return case
