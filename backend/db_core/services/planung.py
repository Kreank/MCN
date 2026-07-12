"""Planungs-Stammdaten-Service: Terminkategorien (workflow.appointment_category)
und Ressourcen (resource.resource) anlegen/ändern/archivieren sowie Ressourcen
einem Einsatz zuordnen (resource.job_resource).

Wie die übrigen Services laufen alle Writes über business_transaction (setzt
app.current_user_id für Audit). Codelisten/Wertebereiche werden vorab geprüft
(klarer ValueError → 422 statt IntegrityError → 500). Statuswechsel spiegeln die
DB-Trigger (0025) und werden dort zusätzlich physisch erzwungen.

Doppelbelegung einer Ressource ist auf DB-Ebene NICHT hart gesperrt (offene
Invariante, siehe Migration 0025): der service_job-Zeitraum ist nullable und
liegt in einer anderen Tabelle, eine saubere EXCLUDE-Constraint ist damit nicht
möglich. `assign_resource` liefert stattdessen einen NICHT-blockierenden
Warnhinweis, wenn sich bekannte Zeitfenster überlappen — die Zuordnung wird
trotzdem angelegt (Hero-Parität: Überlappung sichtbar, aber nicht verboten).
"""
import uuid

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    AppointmentCategory,
    JobAssignment,
    JobResource,
    Resource,
    ServiceJob,
)
from db_core.services._validation import ensure_exists

# Geschlossene Farb-Codeliste (spiegelt den CHECK in Migration 0025). Das UI
# bildet jeden Token WCAG-sicher auf ein Farbschema ab (dekorativer Punkt +
# IMMER den Kategorienamen als Text); freie Hex-Werte sind ausgeschlossen.
COLOR_TOKENS = ("NAVY", "ORANGE", "SAGE", "AMBER", "TEAL", "PLUM", "ROSE", "SLATE")

RESOURCE_TYPES = ("FAHRZEUG", "GERAET", "RAUM", "SONSTIGE")

# Erlaubte Ressourcen-Statuswechsel → {Zielstatus}. Spiegelt den DB-Trigger
# resource.enforce_resource_status.
RESOURCE_TRANSITIONS = {
    "AKTIV": {"INAKTIV"},
    "INAKTIV": {"AKTIV", "ARCHIVIERT"},
    "ARCHIVIERT": set(),
}


# ===========================================================================
# Terminkategorie
# ===========================================================================

def _active_name_taken(name, *, exclude_id=None):
    """True, wenn bereits eine AKTIVE Kategorie den Namen (case-insensitiv)
    trägt — spiegelt den Unique-Index uq_appointment_category_active_name, damit
    die Kollision als klarer ValueError (→422) statt IntegrityError (→500) endet."""
    qs = AppointmentCategory.objects.filter(status="AKTIV", name__iexact=name.strip())
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    return qs.exists()


def create_category(
    actor_app_user_id, *, name, color_token="NAVY", description=None, sort_order=0
):
    """Legt eine Terminkategorie (Status AKTIV) an."""
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    if color_token not in COLOR_TOKENS:
        raise ValueError(
            f"Ungültige color_token '{color_token}'. "
            f"Erlaubt: {', '.join(COLOR_TOKENS)}."
        )
    if _active_name_taken(name):
        raise ValueError(f"Eine aktive Kategorie '{name.strip()}' existiert bereits.")
    if sort_order is None:
        sort_order = 0
    with as_business_error():
        with business_transaction(actor_app_user_id):
            category = AppointmentCategory.objects.create(
                id=uuid.uuid4(),
                name=name.strip(),
                description=(description.strip() if description else None) or None,
                color_token=color_token,
                status="AKTIV",
                sort_order=sort_order,
                created_by_id=actor_app_user_id,
                version=1,
            )
            category.refresh_from_db()
    return category


def update_category(
    actor_app_user_id,
    *,
    category_id,
    name=None,
    color_token=None,
    description=None,
    sort_order=None,
):
    """Ändert Name/Farbe/Beschreibung/Sortierung einer Kategorie. Umbenennen
    wirkt auf bestehende Termine (die FK bleibt bestehen) — wie in Hero."""
    category = AppointmentCategory.objects.filter(id=category_id).first()
    if category is None:
        raise ValueError("Terminkategorie nicht gefunden.")
    if category.status != "AKTIV":
        raise ValueError("Archivierte Kategorien können nicht geändert werden.")
    fields = {}
    if name is not None:
        if not name.strip():
            raise ValueError("name darf nicht leer sein.")
        if _active_name_taken(name, exclude_id=category_id):
            raise ValueError(
                f"Eine aktive Kategorie '{name.strip()}' existiert bereits."
            )
        fields["name"] = name.strip()
    if color_token is not None:
        if color_token not in COLOR_TOKENS:
            raise ValueError(
                f"Ungültige color_token '{color_token}'. "
                f"Erlaubt: {', '.join(COLOR_TOKENS)}."
            )
        fields["color_token"] = color_token
    if description is not None:
        fields["description"] = description.strip() or None
    if sort_order is not None:
        fields["sort_order"] = sort_order
    if not fields:
        return category
    with as_business_error():
        with business_transaction(actor_app_user_id):
            AppointmentCategory.objects.filter(id=category_id).update(**fields)
    category.refresh_from_db()
    return category


def archive_category(actor_app_user_id, *, category_id):
    """Archiviert eine Kategorie (statt Löschen). Bestehende Termine behalten
    die Kategorie; für neue Termine steht sie nicht mehr zur Wahl."""
    category = AppointmentCategory.objects.filter(id=category_id).first()
    if category is None:
        raise ValueError("Terminkategorie nicht gefunden.")
    if category.status == "ARCHIVIERT":
        raise ValueError("Die Kategorie ist bereits archiviert.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            AppointmentCategory.objects.filter(id=category_id).update(
                status="ARCHIVIERT"
            )
    category.refresh_from_db()
    return category


def set_job_category(actor_app_user_id, *, service_job_id, category_id):
    """Setzt oder entfernt (category_id=None) die Terminkategorie eines Einsatzes.

    Nur AKTIVE Kategorien sind zuweisbar; category_id=None löscht die Zuordnung.
    """
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    if category_id is not None:
        category = AppointmentCategory.objects.filter(id=category_id).first()
        if category is None:
            raise ValueError(f"Terminkategorie {category_id} existiert nicht")
        if category.status != "AKTIV":
            raise ValueError("Nur aktive Kategorien können zugewiesen werden.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            ServiceJob.objects.filter(id=service_job_id).update(
                appointment_category_id=category_id
            )
    return ServiceJob.objects.get(id=service_job_id)


# ===========================================================================
# Ressource
# ===========================================================================

def create_resource(actor_app_user_id, *, name, resource_type, notes=None):
    """Legt eine Ressource (Status AKTIV) an."""
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(
            f"Ungültige resource_type '{resource_type}'. "
            f"Erlaubt: {', '.join(RESOURCE_TYPES)}."
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            res = Resource.objects.create(
                id=uuid.uuid4(),
                name=name.strip(),
                resource_type=resource_type,
                status="AKTIV",
                notes=(notes.strip() if notes else None) or None,
                created_by_id=actor_app_user_id,
                version=1,
            )
            res.refresh_from_db()
    return res


def update_resource(
    actor_app_user_id, *, resource_id, name=None, resource_type=None, notes=None
):
    """Ändert Name/Typ/Notiz einer Ressource (nicht den Status — dafür
    set_resource_status)."""
    res = Resource.objects.filter(id=resource_id).first()
    if res is None:
        raise ValueError("Ressource nicht gefunden.")
    if res.status == "ARCHIVIERT":
        raise ValueError("Archivierte Ressourcen können nicht geändert werden.")
    fields = {}
    if name is not None:
        if not name.strip():
            raise ValueError("name darf nicht leer sein.")
        fields["name"] = name.strip()
    if resource_type is not None:
        if resource_type not in RESOURCE_TYPES:
            raise ValueError(
                f"Ungültige resource_type '{resource_type}'. "
                f"Erlaubt: {', '.join(RESOURCE_TYPES)}."
            )
        fields["resource_type"] = resource_type
    if notes is not None:
        fields["notes"] = notes.strip() or None
    if not fields:
        return res
    with as_business_error():
        with business_transaction(actor_app_user_id):
            Resource.objects.filter(id=resource_id).update(**fields)
    res.refresh_from_db()
    return res


def set_resource_status(actor_app_user_id, *, resource_id, to_status):
    """Wechselt den Ressourcenstatus (AKTIV↔INAKTIV, INAKTIV→ARCHIVIERT).

    Der Übergang wird vorab geprüft (→422 statt 500); der DB-Trigger erzwingt ihn
    zusätzlich physisch. 'ARCHIVIERT' entspricht dem Hero-„Entfernen".
    """
    res = Resource.objects.filter(id=resource_id).first()
    if res is None:
        raise ValueError("Ressource nicht gefunden.")
    if to_status not in RESOURCE_TRANSITIONS.get(res.status, set()):
        raise ValueError(
            f"Statuswechsel {res.status} → {to_status} ist nicht zulässig."
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            Resource.objects.filter(id=resource_id).update(status=to_status)
    res.refresh_from_db()
    return res


# ===========================================================================
# Zuordnung Ressource ↔ Einsatz
# ===========================================================================

def _overlap_warnings(service_job, resource_id):
    """Nicht-blockierende Warnhinweise: andere Einsätze, denen dieselbe Ressource
    zugeordnet ist und deren VOLLSTÄNDIG bekannter Zeitraum sich mit dem des
    Einsatzes überlappt. Bei nullable/unvollständigen Zeiträumen wird bewusst
    NICHT gewarnt (keine erfundene Regel)."""
    start, end = service_job.scheduled_start, service_job.scheduled_end
    if start is None or end is None:
        return []
    warnings = []
    others = (
        JobResource.objects.filter(resource_id=resource_id)
        .exclude(service_job_id=service_job.id)
        .select_related("service_job")
    )
    for link in others:
        o = link.service_job
        if o.scheduled_start is None or o.scheduled_end is None:
            continue
        # Halb-offene Intervalle [start, end): Überlappung, wenn a_start < b_end
        # und b_start < a_end.
        if start < o.scheduled_end and o.scheduled_start < end:
            warnings.append(
                f"Ressource ist im selben Zeitfenster bereits Einsatz "
                f"{o.job_number} zugeordnet (Doppelbelegung)."
            )
    return warnings


def belegungs_warnungen(service_job_id):
    """Nicht-blockierende Doppelbelegungs-Hinweise für den AKTUELLEN Zustand
    eines Einsatzes — für Mitarbeiter UND Ressourcen.

    Wird nach einem Umplanen (set_schedule) oder einer Zuweisung gelesen: welche
    anderen Einsätze belegen im neuen Zeitfenster dieselben Personen/Betriebs-
    mittel? Die Doppelbelegung ist **bewusst nicht gesperrt** (offene Invariante,
    siehe Modul-Docstring) — sie wird sichtbar gemacht, nicht verhindert.

    Gewarnt wird nur bei VOLLSTÄNDIG bekannten Zeiträumen (start und end auf
    beiden Seiten). Fehlt ein Ende, ist die Überlappung nicht bestimmbar; eine
    Warnung wäre eine erfundene Regel. Halb-offene Intervalle [start, end).
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None or job.scheduled_start is None or job.scheduled_end is None:
        return []
    start, end = job.scheduled_start, job.scheduled_end
    warnings = []

    # Mitarbeiter: andere Einsätze derselben Zugewiesenen im selben Fenster.
    # Die Zeitfilter schließen NULL-Ränder automatisch aus (NULL vergleicht nie
    # wahr) — genau die gewollte Zurückhaltung.
    user_ids = list(
        JobAssignment.objects.filter(service_job_id=job.id).values_list(
            "assignee_id", flat=True
        )
    )
    if user_ids:
        kollisionen = (
            JobAssignment.objects.filter(assignee_id__in=user_ids)
            .exclude(service_job_id=job.id)
            .filter(
                service_job__scheduled_start__lt=end,
                service_job__scheduled_end__gt=start,
            )
            .select_related("assignee", "service_job")
        )
        for a in kollisionen:
            warnings.append(
                f"{a.assignee.display_name} ist im selben Zeitfenster bereits "
                f"Einsatz {a.service_job.job_number} zugewiesen (Doppelbelegung)."
            )

    # Ressourcen (Betriebsmittel).
    resource_ids = list(
        JobResource.objects.filter(service_job_id=job.id).values_list(
            "resource_id", flat=True
        )
    )
    if resource_ids:
        kollisionen = (
            JobResource.objects.filter(resource_id__in=resource_ids)
            .exclude(service_job_id=job.id)
            .filter(
                service_job__scheduled_start__lt=end,
                service_job__scheduled_end__gt=start,
            )
            .select_related("resource", "service_job")
        )
        for link in kollisionen:
            warnings.append(
                f"Ressource {link.resource.name} ist im selben Zeitfenster "
                f"bereits Einsatz {link.service_job.job_number} zugeordnet "
                f"(Doppelbelegung)."
            )

    # Stabile, doppelfreie Reihenfolge (mehrere Zuweisungen können denselben
    # Fremdeinsatz melden).
    return sorted(set(warnings))


def assign_resource(actor_app_user_id, *, service_job_id, resource_id):
    """Ordnet einem Einsatz eine Ressource zu (resource.job_resource).

    Höchstens ein Eintrag je (Einsatz, Ressource) (DB-UNIQUE). Gibt
    (link, warnings) zurück; warnings ist eine Liste nicht-blockierender
    Doppelbelegungs-Hinweise (die Zuordnung wird dennoch angelegt).
    """
    job = ServiceJob.objects.filter(id=service_job_id).first()
    if job is None:
        raise ValueError(f"Einsatz {service_job_id} existiert nicht")
    res = Resource.objects.filter(id=resource_id).first()
    if res is None:
        raise ValueError(f"Ressource {resource_id} existiert nicht")
    if res.status != "AKTIV":
        raise ValueError("Nur aktive Ressourcen können zugeordnet werden.")
    if JobResource.objects.filter(
        service_job_id=service_job_id, resource_id=resource_id
    ).exists():
        raise ValueError("Diese Ressource ist dem Einsatz bereits zugeordnet.")
    warnings = _overlap_warnings(job, resource_id)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            link = JobResource.objects.create(
                id=uuid.uuid4(),
                service_job_id=service_job_id,
                resource_id=resource_id,
                created_by_id=actor_app_user_id,
            )
    return link, warnings


def unassign_resource(actor_app_user_id, *, service_job_id, resource_id):
    """Entfernt eine Ressourcenzuordnung (nur vor Einsatzabschluss; der
    DB-Trigger blockiert nach ABGESCHLOSSEN/NACHARBEIT → 422)."""
    link = JobResource.objects.filter(
        service_job_id=service_job_id, resource_id=resource_id
    ).first()
    if link is None:
        raise ValueError("Diese Ressource ist dem Einsatz nicht zugeordnet.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            JobResource.objects.filter(id=link.id).delete()
