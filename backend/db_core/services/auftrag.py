"""Auftrags-Service: workflow.work_order anlegen, Beteiligte pflegen,
Verantwortungsbereich bestätigen und Statuswechsel durchführen.

Wie die übrigen Services laufen alle Writes über business_transaction (setzt
app.current_user_id für Audit/Statusprotokoll; bei begründungspflichtigen
Übergängen zusätzlich app.status_reason). Auftragsnummern (AU-…) vergibt die DB
über workflow.next_number; das Model lässt die Spalte ungesetzt (db_default) und
lädt frisch nach.

Der Auftrag hat einen Trigger-gestützten Statusautomaten. Die erlaubten
Übergänge und die Begründungspflicht spiegeln workflow.status_transition
(Migration 0010) — sie werden hier vorab geprüft, damit Eingabefehler als klarer
ValueError (→422) statt als DB-Fehler (→500) enden. Die fachlichen Freigabe-/
Abrechnungs-Tore (Beauftragungsnachweis A-26, bestätigter Verantwortungsbereich
A-21, Auftraggeber A-25, Rechnungsschuldner A-27) setzt die DB als deferred
Constraint-Trigger durch; sie greifen am Transaktionsende.
"""
import uuid

from django.db import IntegrityError
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Project,
    Property,
    ServiceCase,
    ServiceJob,
    TechnicalAsset,
    WorkOrder,
    WorkOrderParty,
)
from db_core.services._validation import ensure_exists, ensure_party_usable


def kundenhistorie(work_order_id):
    """Auftraggeber (PRINCIPAL) des Auftrags samt Kundenhistorie.

    Liefert den primären Auftraggeber und wie viele Aufträge bzw. Einsätze/Termine
    dieser Kunde insgesamt hat (über alle seine Aufträge). „Kunde" = die Party in
    der Rolle PRINCIPAL; ohne Auftraggeber sind die Zähler 0. Rein lesend.
    """
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    principal = (
        WorkOrderParty.objects.filter(work_order_id=work_order_id, role="PRINCIPAL")
        .select_related("party")
        .order_by("-is_primary", "created_at")
        .first()
    )
    if principal is None:
        return {
            "customer_party_id": None,
            "customer_name": None,
            "auftraege_gesamt": 0,
            "termine_gesamt": 0,
        }
    pid = principal.party_id
    auftraege = (
        WorkOrder.objects.filter(parties__party_id=pid, parties__role="PRINCIPAL")
        .distinct()
        .count()
    )
    termine = (
        ServiceJob.objects.filter(
            work_order__parties__party_id=pid, work_order__parties__role="PRINCIPAL"
        )
        .distinct()
        .count()
    )
    return {
        "customer_party_id": pid,
        "customer_name": principal.party.display_name,
        "auftraege_gesamt": auftraege,
        "termine_gesamt": termine,
    }

PRIORITIES = ("NORMAL", "DRINGEND", "NOTFALL")
RESPONSIBILITY_SCOPES = ("UNKNOWN", "COMMON_PROPERTY", "PRIVATE_UNIT", "MIXED")
PARTY_ROLES = (
    "PRINCIPAL",
    "REPRESENTATIVE",
    "SERVICE_RECIPIENT",
    "OCCUPANT",
    "COST_BEARER",
    "INVOICE_DEBTOR",
    "INVOICE_RECIPIENT",
    "REPORTER",
    "ON_SITE_CONTACT",
)
PARTY_SOURCES = ("MANDATE", "OWNERSHIP", "OCCUPANCY", "BILLING_INSTRUCTION", "MANUAL")

# Erlaubte Statusübergänge je Ausgangsstatus → {Zielstatus: begruendungspflichtig}.
# Wörtliche Spiegelung von workflow.status_transition (0010) für entity='work_order'.
WORK_ORDER_TRANSITIONS = {
    "ENTWURF": {"FREIGABE_AUSSTEHEND": False, "FREIGEGEBEN": False, "STORNIERT": True},
    "FREIGABE_AUSSTEHEND": {"ENTWURF": True, "FREIGEGEBEN": False, "STORNIERT": True},
    "FREIGEGEBEN": {"IN_PLANUNG": False, "STORNIERT": True},
    "IN_PLANUNG": {"FREIGEGEBEN": True, "IN_AUSFUEHRUNG": False, "STORNIERT": True},
    "IN_AUSFUEHRUNG": {
        "IN_PLANUNG": True,
        "TECHNISCH_ABGESCHLOSSEN": False,
        "STORNIERT": True,
    },
    "TECHNISCH_ABGESCHLOSSEN": {
        "IN_AUSFUEHRUNG": True,
        "KAUFMAENNISCH_GEPRUEFT": False,
        "STORNIERT": True,
    },
    "KAUFMAENNISCH_GEPRUEFT": {
        "TECHNISCH_ABGESCHLOSSEN": True,
        "ABGERECHNET": False,
        "STORNIERT": True,
    },
    "ABGERECHNET": {},
    "STORNIERT": {},
}


def create_work_order(
    actor_app_user_id,
    *,
    property_id,
    title,
    project_id=None,
    service_case_id=None,
    description=None,
    priority="NORMAL",
    desired_date=None,
    customer_reference=None,
    is_emergency=False,
    asset_id=None,
):
    """Legt einen workflow.work_order (Auftrag) im Initialstatus ENTWURF an.

    property_id ist Pflicht (Liegenschaftsbezug). Der Trigger erzwingt ENTWURF als
    Startstatus; responsibility_scope startet als UNKNOWN und wird später über
    confirm_responsibility bestätigt.

    `asset_id` bindet den Auftrag an eine **technische Anlage** (Therme, Aufzug …).
    Die Spalte liegt seit 0013 in der DB und wurde bis zum Anlagen-Slice von
    **keinem Produktpfad** gesetzt — dasselbe Muster wie `quote.work_order_id`
    (Welle 5): ein Bezug, den nur Tests herstellen, ist im Betrieb keiner. Die DB
    erzwingt über den zusammengesetzten FK (asset_id, property_id), dass die Anlage
    zu dieser Liegenschaft gehört; hier wird es vorab geprüft, damit daraus ein 422
    wird und kein 500.
    """
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
    if priority not in PRIORITIES:
        raise ValueError(
            f"Ungültige priority '{priority}'. Erlaubt: {', '.join(PRIORITIES)}."
        )
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_exists(Project, project_id, "Projekt")
    ensure_exists(ServiceCase, service_case_id, "Vorgang")
    if asset_id is not None:
        asset_property_id = (
            TechnicalAsset.objects.filter(pk=asset_id)
            .values_list("property_id", flat=True)
            .first()
        )
        if asset_property_id is None:
            raise ValueError(f"Anlage {asset_id} existiert nicht")
        if asset_property_id != property_id:
            raise ValueError("Die Anlage gehört nicht zur angegebenen Liegenschaft")

    with business_transaction(actor_app_user_id):
        order = WorkOrder.objects.create(
            id=uuid.uuid4(),
            project_id=project_id,
            service_case_id=service_case_id,
            title=title.strip(),
            description=description,
            property_id=property_id,
            asset_id=asset_id,
            responsibility_scope="UNKNOWN",
            status="ENTWURF",
            priority=priority,
            customer_reference=customer_reference,
            desired_date=desired_date,
            is_emergency=is_emergency,
            version=1,
        )
        order.refresh_from_db()
    return order


def add_work_order_party(
    actor_app_user_id,
    *,
    work_order_id,
    party_id,
    role,
    is_primary=False,
    allocation_percent=None,
    source="MANUAL",
    source_reference_id=None,
):
    """Fügt einen Beteiligten (Rolle) am Auftrag hinzu.

    Höchstens ein primärer Beteiligter je Rolle (DB-Index); doppelte
    (Auftrag, Rolle, Party) sind ausgeschlossen (UNIQUE).
    """
    if role not in PARTY_ROLES:
        raise ValueError(
            f"Ungültige role '{role}'. Erlaubt: {', '.join(PARTY_ROLES)}."
        )
    if source not in PARTY_SOURCES:
        raise ValueError(
            f"Ungültige source '{source}'. Erlaubt: {', '.join(PARTY_SOURCES)}."
        )
    # Anteil außerhalb (0, 100] verletzt sonst work_order_party_allocation_percent_check
    # (IntegrityError → 500); vorab als klaren 422 abweisen.
    if allocation_percent is not None and not (0 < allocation_percent <= 100):
        raise ValueError(
            "Der Anteil muss größer als 0 und höchstens 100 Prozent sein."
        )
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    # party_id muss existieren und darf nicht MERGED sein (trg_work_order_party_no_merged).
    ensure_party_usable(party_id, "Partei")
    try:
        with business_transaction(actor_app_user_id):
            party = WorkOrderParty.objects.create(
                id=uuid.uuid4(),
                work_order_id=work_order_id,
                party_id=party_id,
                role=role,
                source=source,
                source_reference_id=source_reference_id,
                is_primary=is_primary,
                allocation_percent=allocation_percent,
            )
    except IntegrityError as exc:
        msg = str(exc)
        if "uq_work_order_party_primary" in msg:
            raise ValueError(
                "Für diese Rolle ist bereits ein primärer Beteiligter gesetzt; "
                "es kann nur einen geben."
            ) from exc
        if "work_order_party_work_order_id_role_party_id_key" in msg:
            raise ValueError(
                "Diese Partei ist dem Auftrag in dieser Rolle bereits zugeordnet."
            ) from exc
        raise
    return party


def confirm_responsibility(actor_app_user_id, *, work_order_id, scope):
    """Bestätigt den Verantwortungsbereich (A-21): setzt scope + Bestätigungsstempel.

    UNKNOWN kann nicht als bestätigt gelten (DB-Regel); ein späterer Wechsel des
    Bereichs verlangt eine neue Bestätigung — hier immer mitgesetzt.
    """
    if scope not in RESPONSIBILITY_SCOPES:
        raise ValueError(
            f"Ungültiger scope '{scope}'. Erlaubt: {', '.join(RESPONSIBILITY_SCOPES)}."
        )
    if scope == "UNKNOWN":
        raise ValueError("UNKNOWN kann nicht als bestätigte Verantwortung gelten (A-21).")
    with business_transaction(actor_app_user_id):
        updated = WorkOrder.objects.filter(id=work_order_id).update(
            responsibility_scope=scope,
            responsibility_confirmed_at=timezone.now(),
            responsibility_confirmed_by_id=actor_app_user_id,
        )
        if not updated:
            raise ValueError("Auftrag nicht gefunden.")
    return WorkOrder.objects.get(id=work_order_id)


def set_order_evidence(actor_app_user_id, *, work_order_id, reference):
    """Hinterlegt den Beauftragungsnachweis in Textform (A-26)."""
    if not reference or not reference.strip():
        raise ValueError("reference darf nicht leer sein.")
    with business_transaction(actor_app_user_id):
        updated = WorkOrder.objects.filter(id=work_order_id).update(
            order_evidence_reference=reference.strip()
        )
        if not updated:
            raise ValueError("Auftrag nicht gefunden.")
    return WorkOrder.objects.get(id=work_order_id)


def advance_status(actor_app_user_id, *, work_order_id, to_status, reason=None):
    """Führt einen Statuswechsel des Auftrags durch.

    Prüft den Übergang vorab gegen die Übergangstabelle (→422 statt 500) und
    verlangt bei begründungspflichtigen Übergängen einen reason. Die fachlichen
    Tore (Freigabe/Abrechnung) prüft die DB am Transaktionsende.
    """
    order = WorkOrder.objects.filter(id=work_order_id).first()
    if order is None:
        raise ValueError("Auftrag nicht gefunden.")
    allowed = WORK_ORDER_TRANSITIONS.get(order.status, {})
    if to_status not in allowed:
        raise ValueError(
            f"Übergang {order.status} → {to_status} ist nicht erlaubt."
        )
    requires_reason = allowed[to_status]
    if requires_reason and not (reason and reason.strip()):
        raise ValueError(
            f"Übergang {order.status} → {to_status} erfordert eine Begründung."
        )
    with as_business_error():
        with business_transaction(
            actor_app_user_id, status_reason=reason.strip() if reason else None
        ):
            WorkOrder.objects.filter(id=work_order_id).update(status=to_status)
    order.refresh_from_db()
    return order
