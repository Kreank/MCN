"""Einsatz-Service: workflow.service_job anlegen, terminieren, Mitarbeiter
zuweisen, Statuswechsel durchführen und Zeiten/Materialien erfassen.

Wie die übrigen Services laufen alle Writes über business_transaction (setzt
app.current_user_id für Audit/Statusprotokoll; bei begründungspflichtigen
Übergängen zusätzlich app.status_reason). Die Einsatznummer (E-…) vergibt die DB
über workflow.next_number; das Model lässt die Spalte ungesetzt (db_default) und
lädt frisch nach.

Der Einsatz hat einen Trigger-gestützten Statusautomaten (Migration 0014). Die
erlaubten Übergänge und die Begründungspflicht spiegeln workflow.status_transition
(0010) — sie werden hier vorab geprüft, damit Eingabefehler als klarer ValueError
(→422) statt als DB-Fehler (→500) enden. Die fachlichen Tore (Auftragsstatus bei
INSERT, Ausführung ab UNTERWEGS setzt einen freigegebenen Auftrag voraus) setzt
die DB als Trigger durch; sie werden über as_business_error in 422 übersetzt.

Zeit-/Materialerfassung unterliegt dem Korrekturfenster B-28 (Migration 0017):
bis Einsatzabschluss frei; danach nur mit Begründung; nach kaufmännischer
Auftragsprüfung gesperrt. Das setzt ausschließlich die DB durch.
"""
import uuid

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    AppointmentCategory,
    AppUser,
    JobAssignment,
    MaterialEntry,
    ServiceJob,
    TimeEntry,
    WorkOrder,
)
from db_core.services._validation import ensure_exists, ensure_party_usable

ASSIGNMENT_ROLES = ("TECHNICIAN", "LEAD")
TIME_TYPES = (
    "ARBEITSZEIT",
    "FAHRTZEIT",
    "PAUSE",
    "BEREITSCHAFT",
    "NACHARBEIT",
    "INTERNE_ZEIT",
)

# Erlaubte Statusübergänge je Ausgangsstatus → {Zielstatus: begruendungspflichtig}.
# Wörtliche Spiegelung von workflow.status_transition (0010) für entity='service_job'.
SERVICE_JOB_TRANSITIONS = {
    "UNGEPLANT": {"GEPLANT": False},
    "GEPLANT": {"UNGEPLANT": True, "BESTAETIGT": False, "AUSGEFALLEN": True},
    "BESTAETIGT": {"GEPLANT": True, "UNTERWEGS": False, "AUSGEFALLEN": True},
    "UNTERWEGS": {"VOR_ORT": False, "AUSGEFALLEN": True},
    "VOR_ORT": {"PAUSIERT": False, "ABGESCHLOSSEN": False},
    "PAUSIERT": {"VOR_ORT": False},
    "ABGESCHLOSSEN": {"NACHARBEIT": True},
    "NACHARBEIT": {"GEPLANT": False},
    "AUSGEFALLEN": {},
}


def create_service_job(
    actor_app_user_id,
    *,
    work_order_id,
    scheduled_start=None,
    scheduled_end=None,
    on_site_contact_party_id=None,
    access_instructions=None,
    appointment_category_id=None,
):
    """Legt einen workflow.service_job (Einsatz) im Initialstatus UNGEPLANT an.

    work_order_id ist Pflicht. Der Trigger erzwingt UNGEPLANT als Startstatus und
    verhindert die Anlage auf abgerechnete/stornierte Aufträge (B-03/B-06). Ein
    Planungszeitraum darf gleich mitgegeben werden (für den späteren Wechsel nach
    GEPLANT ist scheduled_start ohnehin Pflicht). appointment_category_id ist
    optional; nur AKTIVE Kategorien sind zuweisbar (Migration 0025)."""
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    ensure_party_usable(on_site_contact_party_id, "Ansprechpartner vor Ort")
    if appointment_category_id is not None:
        category = AppointmentCategory.objects.filter(
            id=appointment_category_id
        ).first()
        if category is None:
            raise ValueError(
                f"Terminkategorie {appointment_category_id} existiert nicht"
            )
        if category.status != "AKTIV":
            raise ValueError("Nur aktive Kategorien können zugewiesen werden.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            job = ServiceJob.objects.create(
                id=uuid.uuid4(),
                work_order_id=work_order_id,
                status="UNGEPLANT",
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                on_site_contact_party_id=on_site_contact_party_id,
                access_instructions=access_instructions,
                appointment_category_id=appointment_category_id,
            )
            job.refresh_from_db()
    return job


def set_schedule(
    actor_app_user_id, *, service_job_id, scheduled_start, scheduled_end=None
):
    """Setzt/ändert den Planungszeitraum eines Einsatzes (ohne Statuswechsel).

    Der DB-CHECK verlangt scheduled_end > scheduled_start; ein geplanter/
    bestätigter Einsatz braucht einen scheduled_start (deshalb hier Pflicht).
    """
    if scheduled_start is None:
        raise ValueError("scheduled_start darf nicht leer sein.")
    if scheduled_end is not None and scheduled_end <= scheduled_start:
        raise ValueError("scheduled_end muss nach scheduled_start liegen.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            updated = ServiceJob.objects.filter(id=service_job_id).update(
                scheduled_start=scheduled_start, scheduled_end=scheduled_end
            )
            if not updated:
                raise ValueError("Einsatz nicht gefunden.")
    return ServiceJob.objects.get(id=service_job_id)


def advance_status(actor_app_user_id, *, service_job_id, to_status, reason=None):
    """Führt einen Statuswechsel des Einsatzes durch.

    Prüft den Übergang vorab gegen die Übergangstabelle (→422 statt 500) und
    verlangt bei begründungspflichtigen Übergängen einen reason. Die fachlichen
    Tore (Ausführung ab UNTERWEGS setzt einen freigegebenen Auftrag voraus) prüft
    die DB und wird als 422 übersetzt.
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None:
        raise ValueError("Einsatz nicht gefunden.")
    allowed = SERVICE_JOB_TRANSITIONS.get(job.status, {})
    if to_status not in allowed:
        raise ValueError(
            f"Übergang {job.status} → {to_status} ist nicht erlaubt."
        )
    requires_reason = allowed[to_status]
    if requires_reason and not (reason and reason.strip()):
        raise ValueError(
            f"Übergang {job.status} → {to_status} erfordert eine Begründung."
        )
    with as_business_error():
        with business_transaction(
            actor_app_user_id, status_reason=reason.strip() if reason else None
        ):
            ServiceJob.objects.filter(id=service_job_id).update(status=to_status)
    job.refresh_from_db()
    return job


def assign_user(actor_app_user_id, *, service_job_id, assignee_user_id, role="TECHNICIAN"):
    """Weist dem Einsatz einen Mitarbeiter (security.app_user) zu.

    Höchstens ein Eintrag je (Einsatz, Mitarbeiter) (DB-UNIQUE). Rolle TECHNICIAN
    (Standard) oder LEAD.
    """
    if role not in ASSIGNMENT_ROLES:
        raise ValueError(
            f"Ungültige role '{role}'. Erlaubt: {', '.join(ASSIGNMENT_ROLES)}."
        )
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    ensure_exists(AppUser, assignee_user_id, "Mitarbeiter")
    # Doppelzuweisung verletzt sonst den UNIQUE(service_job_id, assignee_user_id).
    if JobAssignment.objects.filter(
        service_job_id=service_job_id, assignee_id=assignee_user_id
    ).exists():
        raise ValueError("Dieser Mitarbeiter ist dem Einsatz bereits zugewiesen.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            assignment = JobAssignment.objects.create(
                id=uuid.uuid4(),
                service_job_id=service_job_id,
                assignee_id=assignee_user_id,
                role=role,
            )
    return assignment


def log_time(
    actor_app_user_id,
    *,
    service_job_id,
    user_id,
    time_type,
    started_at,
    ended_at,
    note=None,
):
    """Erfasst eine Zeit am Einsatz (B-27). ended_at muss nach started_at liegen.

    Das Korrekturfenster (B-28) prüft die DB: bis Einsatzabschluss frei, danach
    nur mit Begründung, nach kaufmännischer Auftragsprüfung gesperrt.
    """
    if time_type not in TIME_TYPES:
        raise ValueError(
            f"Ungültige time_type '{time_type}'. Erlaubt: {', '.join(TIME_TYPES)}."
        )
    if ended_at <= started_at:
        raise ValueError("ended_at muss nach started_at liegen.")
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    ensure_exists(AppUser, user_id, "Mitarbeiter")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            entry = TimeEntry.objects.create(
                id=uuid.uuid4(),
                service_job_id=service_job_id,
                user_id=user_id,
                time_type=time_type,
                started_at=started_at,
                ended_at=ended_at,
                note=note,
            )
    return entry


def log_material(
    actor_app_user_id,
    *,
    service_job_id,
    description,
    quantity,
    unit,
    recorded_by,
    note=None,
):
    """Erfasst einen Materialverbrauch am Einsatz (B-26: reine Verbrauchserfassung,
    keine Bestandsführung). quantity muss > 0 sein; das Korrekturfenster (B-28)
    prüft die DB."""
    if not description or not description.strip():
        raise ValueError("description darf nicht leer sein.")
    if not unit or not unit.strip():
        raise ValueError("unit darf nicht leer sein.")
    if quantity is None or quantity <= 0:
        raise ValueError("quantity muss größer als 0 sein.")
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    ensure_exists(AppUser, recorded_by, "Erfasser")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            entry = MaterialEntry.objects.create(
                id=uuid.uuid4(),
                service_job_id=service_job_id,
                description=description.strip(),
                quantity=quantity,
                unit=unit.strip(),
                recorded_by_id=recorded_by,
                note=note,
            )
    return entry
