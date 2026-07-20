"""Projekt-Service: Projekte, Projekt↔Liegenschaft, Vorgänge anlegen.

Wie die übrigen Services laufen alle Writes über business_transaction (setzt
app.current_user_id für Audit/Statusprotokoll). Projekt-Nummern (P-HZG-26-…) und
Vorgangsnummern (V-HZG-26-…) vergibt die DB per BEFORE-INSERT-Trigger aus dem
Gewerk (Migration 0120); die Models lassen die Spalten ungesetzt und laden
frisch nach.

Der Projekt-„Status" ist nur OPEN/CLOSED (kein Statusautomat). Der Vorgang
(service_case) hat dagegen einen Trigger-gestützten Statusautomaten; hier wird
nur der Initialzustand NEU angelegt — Statuswechsel folgen als eigener Slice.
"""
import uuid

from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    AppUser,
    Checklist,
    ChecklistItem,
    Invoice,
    Project,
    ProjectCategory,
    ProjectLog,
    ProjectProperty,
    Property,
    Quote,
    ServiceCase,
    StatusCatalog,
    StatusTransition,
    Trade,
    WorkOrder,
)
from db_core.services import beleg as beleg_service
from db_core.services._validation import (
    ensure_all_exist,
    ensure_exists,
    ensure_party_usable,
)

LOG_CATEGORIES = ("NOTIZ", "ANRUF", "ABSPRACHE", "ENTSCHEIDUNG", "SYSTEM")

PRIORITIES = ("NORMAL", "DRINGEND", "NOTFALL")

SERVICE_CASE_ENTITY = "service_case"

# Endstatus eines Vorgangs für die Board-Vorauswahl. Der Statuskatalog markiert
# für service_case kein is_final (0042 setzt es nur für work_order/quote), fachlich
# sind ABGESCHLOSSEN und ABGELEHNT aber die beiden Endpunkte des Lebenszyklus
# (sort_order 6/7): ABGELEHNT ist eine Senke ohne ausgehende Kante, ABGESCHLOSSEN
# kann nur mit Begründung wiedereröffnet werden. Das Board blendet ihre Karten
# per Default aus (offene Vorgänge), zeigt die Spalten aber weiter als Drop-Ziele.
TERMINAL_SERVICE_CASE_STATUSES = ("ABGESCHLOSSEN", "ABGELEHNT")


def service_case_board_columns():
    """Spalten des Vorgangs-Boards: der Statuskatalog (entity=service_case) nach
    sort_order. Liefert Dicts {status, label, sort_order, is_final, is_terminal}.

    Read-only Stammdaten; die Reihenfolge/Labels stammen aus workflow.status_catalog
    (Pipeline-Editor 0042), damit eine geänderte Pipeline sofort auf dem Board wirkt.
    is_terminal markiert die per Default ausgeblendeten Endspalten.
    """
    rows = StatusCatalog.objects.filter(entity=SERVICE_CASE_ENTITY).order_by("sort_order")
    return [
        {
            "status": c.status,
            "label": c.label,
            "sort_order": c.sort_order,
            "is_final": c.is_final,
            "is_terminal": c.status in TERMINAL_SERVICE_CASE_STATUSES,
        }
        for c in rows
    ]


def create_project(
    actor_app_user_id,
    *,
    name,
    category_id=None,
    property_ids=None,
    start_date=None,
    target_end_date=None,
    responsible_user_id=None,
    trade_id=None,
):
    """Legt ein workflow.project an und verknüpft optional Liegenschaften.

    Gibt das frisch nachgeladene Projekt zurück (mit vergebener Projektnummer).
    """
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    ensure_exists(ProjectCategory, category_id, "Kategorie")
    ensure_exists(AppUser, responsible_user_id, "Benutzer")
    ensure_exists(Trade, trade_id, "Gewerk")
    ensure_all_exist(Property, property_ids, "Liegenschaft")

    with business_transaction(actor_app_user_id):
        project = Project.objects.create(
            id=uuid.uuid4(),
            name=name.strip(),
            status="OPEN",
            start_date=start_date,
            target_end_date=target_end_date,
            responsible_user_id=responsible_user_id,
            category_id=category_id,
            trade_id=trade_id,
            version=1,
        )
        for property_id in property_ids or []:
            ProjectProperty.objects.create(
                project_id=project.id, property_id=property_id
            )
        project.refresh_from_db()
    return project


def set_project_responsible(actor_app_user_id, *, project_id, responsible_user_id):
    """Setzt (oder entfernt) den Verantwortlichen eines Projekts.

    `responsible_user_id=None` entfernt die Zuweisung. **Kein Schemawechsel** — die
    Spalte workflow.project.responsible_user_id existiert seit der Projekt-Baseline
    und wurde beim Anlegen schon unterstützt; hier wird sie nur additiv über einen
    eigenen Pfad beschreibbar. Das UPDATE ist erlaubt (kein No-Update-Trigger auf
    der Spalte; Updates werden auditiert). Gibt das frisch geladene Projekt zurück.
    """
    ensure_exists(Project, project_id, "Projekt")
    ensure_exists(AppUser, responsible_user_id, "Benutzer")
    with business_transaction(actor_app_user_id):
        Project.objects.filter(id=project_id).update(
            responsible_user_id=responsible_user_id
        )
    return (
        Project.objects.select_related("responsible_user", "category")
        .get(id=project_id)
    )


def set_project_internal_note(actor_app_user_id, *, project_id, internal_note):
    """Setzt das freie Notizfeld eines Projekts (workflow.project.internal_note).

    Freitext getrennt vom Logbuch (Hero-Angleichung Projekte-7). Leerer/blanker
    Text wird zu NULL normalisiert, damit „gelöscht" und „nie gesetzt" gleich
    aussehen. Additive Spalte, kein No-Update-Trigger; Updates werden auditiert.
    Gibt das frisch geladene Projekt zurück.
    """
    ensure_exists(Project, project_id, "Projekt")
    wert = internal_note.strip() if internal_note and internal_note.strip() else None
    with business_transaction(actor_app_user_id):
        Project.objects.filter(id=project_id).update(internal_note=wert)
    return (
        Project.objects.select_related("responsible_user", "category")
        .get(id=project_id)
    )


def promote_service_case_to_project(actor_app_user_id, *, service_case_id, name=None):
    """Stuft einen Vorgang zum Projekt hoch: legt ein neues Projekt an und hängt
    den Vorgang, alle seine Aufträge UND deren Belege darunter (project_id setzen).

    Das Projekt ist die optionale Klammer (B-09); dieser Weg fügt sie nachträglich
    hinzu, wenn ein zunächst kleiner Vorgang wächst. Nur zulässig, solange der
    Vorgang noch KEIN Projekt hat. Das neue Projekt umfasst die Liegenschaften des
    Vorgangs und aller Aufträge (die abweichen können, B-10). Umgehängt werden nur
    Aufträge ohne eigenes Projekt (ein Auftrag eines anderen Projekts wird nicht
    „gestohlen").

    **Belege (Migration 0113):** Zusätzlich werden alle projektlosen, noch
    änderbaren Angebote/Rechnungen mitgezogen, die entweder DIREKT am Vorgang hängen
    (`service_case_id = case`) ODER an einem der in diesem Aufruf umgehängten
    (projektlosen) Aufträge. Belege fremder Projekte und die Belege von Aufträgen
    fremder Projekte bleiben unberührt (`project_id IS NULL` filtert sie aus, und die
    Auftragsmenge ist ausschließlich die projektlose). Versendete Angebote und
    veröffentlichte Rechnungen sind eingefroren (B-30/GoBD) — ihr `project_id` lässt
    sich nicht mehr ändern; sie bleiben projektlos, statt die Aufstufung abzubrechen.

    Alles läuft in EINER Transaktion; der interne create_project öffnet dabei einen
    Savepoint. Das Setzen von project_id ist per UPDATE erlaubt (kein Immutable-/
    No-Update-Trigger auf der Spalte). Ohne `name` wird der Vorgangsbetreff genutzt.
    """
    case = ServiceCase.objects.filter(id=service_case_id).first()
    if case is None:
        raise ValueError("Vorgang nicht gefunden.")
    if case.project_id:
        raise ValueError("Der Vorgang hängt bereits an einem Projekt.")
    projektname = (name or "").strip() or case.subject
    # Nur Liegenschaften der Objekte, die auch umgehängt werden (Vorgang + seine
    # projektlosen Aufträge) — deckungsgleich mit der Umhäng-Menge unten.
    property_ids = list(
        {case.property_id}
        | set(
            WorkOrder.objects.filter(
                service_case_id=service_case_id, project_id__isnull=True
            ).values_list("property_id", flat=True)
        )
    )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            project = create_project(
                actor_app_user_id, name=projektname, property_ids=property_ids
            )
            # Race-sicher: nur umhängen, solange der Vorgang wirklich projektlos ist.
            umgehaengt = (
                ServiceCase.objects.filter(
                    id=service_case_id, project_id__isnull=True
                ).update(project_id=project.id)
            )
            if umgehaengt != 1:
                raise ValueError("Der Vorgang hängt bereits an einem Projekt.")
            # Die projektlosen Aufträge dieses Vorgangs — ihre IDs VOR dem Umhängen
            # festhalten, damit die daran hängenden Belege mitgezogen werden können.
            auftrag_ids = list(
                WorkOrder.objects.filter(
                    service_case_id=service_case_id, project_id__isnull=True
                ).values_list("id", flat=True)
            )
            WorkOrder.objects.filter(id__in=auftrag_ids).update(project_id=project.id)
            # Belege: alle projektlosen Angebote/Rechnungen, die direkt am Vorgang
            # ODER an einem der eben umgehängten Aufträge hängen. Fremde Projekte
            # bleiben unberührt (project_id__isnull=True + nur die projektlose
            # Auftragsmenge).
            beleg_filter = Q(service_case_id=service_case_id) | Q(
                work_order_id__in=auftrag_ids
            )
            # NUR noch änderbare Belege umhängen: ein versendetes Angebot
            # (freeze_sent_quote friert alles außer status/work_order_id ein) bzw.
            # eine veröffentlichte Rechnung (freeze_published_invoice, GoBD B-21)
            # ließen sich physisch nicht mehr ändern — der Versuch bräche die ganze
            # Aufstufung ab. Sie bleiben projektlos; project_id ist dort Beleghistorie.
            Quote.objects.filter(
                beleg_filter,
                project_id__isnull=True,
                status__in=beleg_service.QUOTE_EDITIERBAR,
            ).update(project_id=project.id)
            Invoice.objects.filter(
                beleg_filter,
                project_id__isnull=True,
                status__in=beleg_service.INVOICE_EDITIERBAR,
            ).update(project_id=project.id)
    return project


def add_project_log(actor_app_user_id, *, project_id, entry, category="NOTIZ"):
    """Fügt einen Logbuch-Eintrag am Projekt hinzu (append-only)."""
    if category not in LOG_CATEGORIES:
        raise ValueError(
            f"Ungültige category '{category}'. Erlaubt: {', '.join(LOG_CATEGORIES)}."
        )
    if not entry or not entry.strip():
        raise ValueError("entry darf nicht leer sein.")
    ensure_exists(Project, project_id, "Projekt")
    with business_transaction(actor_app_user_id):
        log = ProjectLog.objects.create(
            id=uuid.uuid4(),
            project_id=project_id,
            category=category,
            entry=entry.strip(),
            created_by_id=actor_app_user_id,
        )
    return log


def create_checklist(actor_app_user_id, *, project_id, name, items=None):
    """Legt eine Checkliste mit Punkten (offen) an einem Projekt an.

    items: Liste von Labels (Strings). Positionen werden 1..N vergeben.
    """
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    ensure_exists(Project, project_id, "Projekt")
    with business_transaction(actor_app_user_id):
        checklist = Checklist.objects.create(
            id=uuid.uuid4(),
            project_id=project_id,
            name=name.strip(),
            created_by_id=actor_app_user_id,
        )
        for pos, label in enumerate(items or [], start=1):
            ChecklistItem.objects.create(
                id=uuid.uuid4(),
                checklist_id=checklist.id,
                position=pos,
                label=label.strip(),
            )
    return checklist


def create_service_case(
    actor_app_user_id,
    *,
    property_id,
    subject,
    project_id=None,
    description=None,
    reported_by_party_id=None,
    priority="NORMAL",
    trade_id=None,
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
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_exists(Project, project_id, "Projekt")
    ensure_party_usable(reported_by_party_id, "Melder")
    ensure_exists(Trade, trade_id, "Gewerk")
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
            trade_id=trade_id,
            version=1,
        )
        case.refresh_from_db()
    return case


def service_case_status_recht(to_status):
    """Recht, das ein Vorgangs-Statuswechsel verlangt.

    Der Übergang FREIGABE_AUSSTEHEND → BEAUFTRAGT ist die eigentliche
    Beauftragung/Freigabe des Vorgangs (aus ihm entsteht der Auftrag); das ist
    ein Freigabetor und verlangt workflow.FREIGEBEN. Alle übrigen Wechsel sind
    workflow.AENDERN. BEAUFTRAGT ist im Statusmodell nur aus FREIGABE_AUSSTEHEND
    erreichbar (0010), daher genügt das Ziel als Kriterium — wie beim Auftrag
    (FREIGEGEBEN). So kann ein Konto mit AENDERN, aber ohne FREIGEBEN, den
    Vorgang nicht beauftragen.
    """
    return "FREIGEBEN" if to_status == "BEAUFTRAGT" else "AENDERN"


def service_case_transitions(from_status):
    """Erlaubte nächste Status eines Vorgangs im gegebenen Ausgangsstatus.

    Liest die Kanten ZUR LAUFZEIT aus workflow.status_transition und reichert
    Label + Reihenfolge aus workflow.status_catalog an. Rückgabe: nach
    sort_order sortierte Liste von Dicts {to_status, label, reason_required,
    recht} (recht = das je Übergang nötige Modulrecht, s. o.).
    """
    catalog = {
        c.status: c for c in StatusCatalog.objects.filter(entity=SERVICE_CASE_ENTITY)
    }
    rows = []
    for edge in StatusTransition.objects.filter(
        entity=SERVICE_CASE_ENTITY, from_status=from_status
    ):
        cat = catalog.get(edge.to_status)
        rows.append(
            {
                "to_status": edge.to_status,
                "label": cat.label if cat else edge.to_status,
                "reason_required": edge.requires_reason,
                "recht": service_case_status_recht(edge.to_status),
                "_sort": cat.sort_order if cat else 0,
            }
        )
    rows.sort(key=lambda r: r["_sort"])
    for r in rows:
        del r["_sort"]
    return rows


def advance_service_case_status(
    actor_app_user_id, *, service_case_id, to_status, reason=None
):
    """Führt einen Statuswechsel des Vorgangs (service_case) durch.

    Prüft den Übergang vorab gegen workflow.status_transition (→ ValueError/422
    statt DB-500) und verlangt bei begründungspflichtigen Übergängen einen
    reason. Der DB-Trigger validate_status_change ist die maßgebliche Instanz;
    scheitert er (z. B. weil die Pipeline nebenläufig geändert wurde), übersetzt
    as_business_error den P0001 in einen ValueError (→422).

    Unbekannter Vorgang → ValueError; die API prüft die Existenz vorab und
    antwortet dort mit 404.
    """
    case = ServiceCase.objects.filter(id=service_case_id).first()
    if case is None:
        raise ValueError("Vorgang nicht gefunden.")
    allowed = {
        e.to_status: e.requires_reason
        for e in StatusTransition.objects.filter(
            entity=SERVICE_CASE_ENTITY, from_status=case.status
        )
    }
    if to_status not in allowed:
        raise ValueError(f"Übergang {case.status} → {to_status} ist nicht erlaubt.")
    if allowed[to_status] and not (reason and reason.strip()):
        raise ValueError(
            f"Übergang {case.status} → {to_status} erfordert eine Begründung."
        )
    with as_business_error():
        with business_transaction(
            actor_app_user_id, status_reason=reason.strip() if reason else None
        ):
            ServiceCase.objects.filter(id=service_case_id).update(status=to_status)
    case.refresh_from_db()
    return case
