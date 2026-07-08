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

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import WorkOrder, WorkOrderParty

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
):
    """Legt einen workflow.work_order (Auftrag) im Initialstatus ENTWURF an.

    property_id ist Pflicht (Liegenschaftsbezug). Der Trigger erzwingt ENTWURF als
    Startstatus; responsibility_scope startet als UNKNOWN und wird später über
    confirm_responsibility bestätigt.
    """
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
    if priority not in PRIORITIES:
        raise ValueError(
            f"Ungültige priority '{priority}'. Erlaubt: {', '.join(PRIORITIES)}."
        )

    with business_transaction(actor_app_user_id):
        order = WorkOrder.objects.create(
            id=uuid.uuid4(),
            project_id=project_id,
            service_case_id=service_case_id,
            title=title.strip(),
            description=description,
            property_id=property_id,
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
