"""Einsatz-Service: workflow.service_job anlegen, terminieren, Mitarbeiter
zuweisen, Statuswechsel durchführen und Zeiten/Materialien erfassen.

Wie die übrigen Services laufen alle Writes über business_transaction (setzt
app.current_user_id für Audit/Statusprotokoll; bei begründungspflichtigen
Übergängen zusätzlich app.status_reason). Die Einsatznummer (E-…) vergibt die DB
über workflow.next_number; das Model lässt die Spalte ungesetzt (db_default) und
lädt frisch nach.

Seit Migration 0062 gibt es zwei Spielarten: den auftragsgebundenen Einsatz und
den **freien Termin** (work_order_id IS NULL — Begehung/Besichtigung/Beratung vor
der Beauftragung). Der freie Termin braucht einen eigenen Titel, darf optional an
einer Liegenschaft hängen und läuft ohne das Auftrags-Ausführungstor.

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
    Property,
    ServiceJob,
    TimeEntry,
    WorkOrder,
)
from db_core.services._validation import ensure_exists, ensure_party_usable

# Sentinel für Teil-Updates: „Feld nicht mitgeschickt" ist etwas anderes als
# „Feld ausdrücklich auf NULL setzen" (z. B. Kontakt wieder entfernen).
_UNSET = object()

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


def _check_category(appointment_category_id):
    if appointment_category_id is None:
        return
    category = AppointmentCategory.objects.filter(id=appointment_category_id).first()
    if category is None:
        raise ValueError(f"Terminkategorie {appointment_category_id} existiert nicht")
    if category.status != "AKTIV":
        raise ValueError("Nur aktive Kategorien können zugewiesen werden.")


def _check_property_matches_order(work_order_id, property_id):
    """Liegenschaft am Einsatz muss zum Auftrag passen (DB: zusammengesetzter FK).

    Vorabprüfung, damit der Verstoß als klarer 422 statt als IntegrityError (500)
    endet. Ohne Auftrag (freier Termin) ist jede existierende Liegenschaft ok.
    """
    if property_id is None:
        return
    ensure_exists(Property, property_id, "Liegenschaft")
    if work_order_id is None:
        return
    order_property_id = (
        WorkOrder.objects.filter(id=work_order_id)
        .values_list("property_id", flat=True)
        .first()
    )
    if order_property_id != property_id:
        raise ValueError(
            "Die Liegenschaft des Einsatzes muss die Liegenschaft des Auftrags sein."
        )


def _clean_title(title):
    """Leerstring/Whitespace → None (der DB-CHECK verbietet leere Titel)."""
    if title is None:
        return None
    cleaned = title.strip()
    return cleaned or None


def create_service_job(
    actor_app_user_id,
    *,
    work_order_id=None,
    title=None,
    property_id=None,
    scheduled_start=None,
    scheduled_end=None,
    on_site_contact_party_id=None,
    access_instructions=None,
    appointment_category_id=None,
):
    """Legt einen workflow.service_job (Einsatz) im Initialstatus UNGEPLANT an.

    Zwei Spielarten (Migration 0062):

    * **auftragsgebunden** (work_order_id gesetzt): wie bisher. Der Trigger
      verhindert die Anlage auf abgerechnete/stornierte Aufträge (B-03/B-06).
      `title` ist optional (Fallback: Auftragstitel); `property_id` ist optional
      und muss, wenn gesetzt, die Liegenschaft des Auftrags sein.
    * **freier Termin** (work_order_id=None): Begehung/Besichtigung/Beratung ohne
      Auftrag. `title` ist dann **Pflicht**; `property_id` und der Kontakt
      (on_site_contact_party_id) bleiben optional — bei einer Begehung ist der
      Kunde oft noch gar nicht angelegt und wird über update_service_job
      nachgetragen.

    Der Trigger erzwingt UNGEPLANT als Startstatus. appointment_category_id ist
    optional; nur AKTIVE Kategorien sind zuweisbar (Migration 0025).
    """
    title = _clean_title(title)
    if work_order_id is None and title is None:
        raise ValueError("Ein freier Termin ohne Auftrag braucht einen Titel.")
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    _check_property_matches_order(work_order_id, property_id)
    ensure_party_usable(on_site_contact_party_id, "Ansprechpartner vor Ort")
    _check_category(appointment_category_id)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            job = ServiceJob.objects.create(
                id=uuid.uuid4(),
                work_order_id=work_order_id,
                title=title,
                property_id=property_id,
                status="UNGEPLANT",
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                on_site_contact_party_id=on_site_contact_party_id,
                access_instructions=access_instructions,
                appointment_category_id=appointment_category_id,
            )
            job.refresh_from_db()
    return job


def update_service_job(
    actor_app_user_id,
    *,
    service_job_id,
    on_site_contact_party_id=_UNSET,
    title=_UNSET,
    property_id=_UNSET,
    access_instructions=_UNSET,
):
    """Trägt Stammangaben eines Einsatzes nach (Teil-Update, Sentinel-basiert).

    Hauptzweck: den **Ansprechpartner vor Ort nachtragen**. Bei einer Begehung
    steht der Kontakt oft erst nach dem Termin fest (oder existiert im System
    noch gar nicht) — deshalb muss er nachträglich setzbar sein. Zusätzlich
    lassen sich Titel, Liegenschaft und Zutrittshinweise korrigieren.

    Nicht mitgegebene Felder bleiben unangetastet; ausdrückliches ``None`` löscht
    das Feld (Kontakt entfernen). Der Auftragsbezug ist NICHT änderbar (DB-Trigger
    WF-01): ein freier Termin bleibt frei.

    Regeln (→ ValueError = 422):
    * Ein freier Termin darf seinen Titel nicht verlieren.
    * Eine Liegenschaft muss bei auftragsgebundenen Einsätzen die des Auftrags
      sein.
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None:
        raise ValueError("Einsatz nicht gefunden.")

    felder = {}
    if title is not _UNSET:
        neuer_titel = _clean_title(title)
        if neuer_titel is None and job.work_order_id is None:
            raise ValueError("Ein freier Termin ohne Auftrag braucht einen Titel.")
        felder["title"] = neuer_titel
    if property_id is not _UNSET:
        _check_property_matches_order(job.work_order_id, property_id)
        felder["property_id"] = property_id
    if on_site_contact_party_id is not _UNSET:
        ensure_party_usable(on_site_contact_party_id, "Ansprechpartner vor Ort")
        felder["on_site_contact_party_id"] = on_site_contact_party_id
    if access_instructions is not _UNSET:
        felder["access_instructions"] = (
            access_instructions.strip() or None
            if isinstance(access_instructions, str)
            else access_instructions
        )
    if not felder:
        return job

    with as_business_error():
        with business_transaction(actor_app_user_id):
            ServiceJob.objects.filter(id=service_job_id).update(**felder)
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


def unassign_user(actor_app_user_id, *, service_job_id, assignee_user_id):
    """Hebt die Zuweisung eines Mitarbeiters am Einsatz auf (Korrektur).

    Gegenstück zu assign_user — nötig, um einen Einsatz auf der Plantafel von
    einer Bahn in eine andere zu ziehen (die alte Zuweisung muss weichen).

    Der DB-Trigger `workflow.protect_job_assignment` lässt das Löschen nur zu,
    solange der Einsatz nicht ABGESCHLOSSEN/NACHARBEIT ist (Historienschutz
    F-02); danach kommt der Fehler als fachlicher 422 zurück, NICHT als 500.
    """
    link = JobAssignment.objects.filter(
        service_job_id=service_job_id, assignee_id=assignee_user_id
    ).first()
    if link is None:
        raise ValueError("Dieser Mitarbeiter ist dem Einsatz nicht zugewiesen.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            JobAssignment.objects.filter(id=link.id).delete()


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
