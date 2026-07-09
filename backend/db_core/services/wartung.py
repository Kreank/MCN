"""Wartungs-Service: maintenance.maintenance_contract anlegen, Status wechseln
und Fälligkeits-Aktionen auslösen.

Wie die übrigen Services laufen alle Writes über business_transaction. Die
Vertragsnummer (W-…) vergibt die DB (db_default → refresh_from_db). Der
Statusautomat (AKTIV ↔ INAKTIV, INAKTIV → ARCHIVIERT) wird hier vorab geprüft
(klarer ValueError → 422) und von einem maintenance-eigenen Trigger physisch
erzwungen; Trigger-Verstöße übersetzt as_business_error in 422.

Fälligkeits-Aktion: trigger_action protokolliert die Auslösung append-only in
maintenance.maintenance_event und rückt next_due_date um ein Intervall vor. Für
die Aktion AUFGABE wird zusätzlich eine workflow.task erzeugt (konkretes
Folgeobjekt); die übrigen Aktionen (PROJEKT/AUFTRAG/BENACHRICHTIGUNG) werden
vorerst nur protokolliert — der automatische Fälligkeits-Scheduler folgt später.
"""
import calendar
import uuid
from datetime import date, timedelta

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import MaintenanceContract, MaintenanceEvent
from db_core.services import aufgabe as aufgabe_service

INTERVAL_KINDS = ("JAEHRLICH", "MONATLICH", "WOECHENTLICH", "TAGE", "FESTES_DATUM")
DUE_ACTIONS = ("PROJEKT", "AUFTRAG", "AUFGABE", "BENACHRICHTIGUNG")

# Erlaubte Statusübergänge → {Zielstatus}. AKTIV/INAKTIV frei wechselbar,
# ARCHIVIERT nur aus INAKTIV und final (kein Rücksprung). Spiegelt den
# DB-Trigger maintenance.enforce_contract_status.
CONTRACT_TRANSITIONS = {
    "AKTIV": {"INAKTIV"},
    "INAKTIV": {"AKTIV", "ARCHIVIERT"},
    "ARCHIVIERT": set(),
}


def _add_months(d, n):
    """Addiert n Monate mit Tages-Clamping (31.01. + 1 Monat → 28./29.02.)."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _initial_due(*, start_date, interval_kind, fixed_date):
    """Erste Fälligkeit: bei FESTES_DATUM das feste Datum, sonst das Startdatum."""
    if interval_kind == "FESTES_DATUM":
        return fixed_date
    return start_date


def _advance_due(contract):
    """Nächste Fälligkeit nach einer ausgelösten Aktion (None = einmalig, fertig)."""
    base = contract.next_due_date or contract.start_date
    kind = contract.interval_kind
    if kind == "JAEHRLICH":
        return _add_months(base, 12)
    if kind == "MONATLICH":
        return _add_months(base, 1)
    if kind == "WOECHENTLICH":
        return base + timedelta(weeks=1)
    if kind == "TAGE":
        return base + timedelta(days=contract.interval_days)
    return None  # FESTES_DATUM: einmalige Fälligkeit


def create_contract(
    actor_app_user_id,
    *,
    property_id,
    name,
    start_date,
    interval_kind,
    due_action,
    interval_days=None,
    fixed_date=None,
    party_id=None,
    project_id=None,
    lead_time_days=None,
    notes=None,
):
    """Legt einen Wartungsvertrag im Status AKTIV an und berechnet die erste
    Fälligkeit. property_id ist Pflicht (Liegenschaftsbezug)."""
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    if interval_kind not in INTERVAL_KINDS:
        raise ValueError(
            f"Ungültige interval_kind '{interval_kind}'. "
            f"Erlaubt: {', '.join(INTERVAL_KINDS)}."
        )
    if due_action not in DUE_ACTIONS:
        raise ValueError(
            f"Ungültige due_action '{due_action}'. Erlaubt: {', '.join(DUE_ACTIONS)}."
        )
    if interval_kind == "TAGE" and not interval_days:
        raise ValueError("interval_kind 'TAGE' erfordert interval_days > 0.")
    if interval_kind == "FESTES_DATUM" and fixed_date is None:
        raise ValueError("interval_kind 'FESTES_DATUM' erfordert fixed_date.")
    if lead_time_days is not None and lead_time_days < 0:
        raise ValueError("lead_time_days darf nicht negativ sein.")

    next_due = _initial_due(
        start_date=start_date, interval_kind=interval_kind, fixed_date=fixed_date
    )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            contract = MaintenanceContract.objects.create(
                id=uuid.uuid4(),
                name=name.strip(),
                property_id=property_id,
                party_id=party_id,
                project_id=project_id,
                status="AKTIV",
                start_date=start_date,
                interval_kind=interval_kind,
                interval_days=interval_days,
                fixed_date=fixed_date,
                next_due_date=next_due,
                due_action=due_action,
                lead_time_days=lead_time_days,
                notes=notes,
                created_by_id=actor_app_user_id,
                version=1,
            )
            contract.refresh_from_db()
    return contract


def set_status(actor_app_user_id, *, contract_id, to_status):
    """Wechselt den Vertragsstatus (AKTIV↔INAKTIV, INAKTIV→ARCHIVIERT).

    Der Übergang wird vorab geprüft (→422 statt 500); der DB-Trigger erzwingt ihn
    zusätzlich physisch.
    """
    contract = MaintenanceContract.objects.filter(id=contract_id).first()
    if contract is None:
        raise ValueError("Wartungsvertrag nicht gefunden.")
    if to_status not in CONTRACT_TRANSITIONS.get(contract.status, set()):
        raise ValueError(
            f"Statuswechsel {contract.status} → {to_status} ist nicht zulässig."
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            MaintenanceContract.objects.filter(id=contract_id).update(status=to_status)
    contract.refresh_from_db()
    return contract


def trigger_action(actor_app_user_id, *, contract_id, note=None):
    """Löst die Fälligkeits-Aktion des Vertrags manuell aus.

    Protokolliert die Auslösung append-only und rückt next_due_date vor. Nur auf
    aktiven Verträgen möglich. Für die Aktion AUFGABE wird zusätzlich eine
    workflow.task erzeugt und als Folgeobjekt am Event vermerkt.
    """
    contract = MaintenanceContract.objects.filter(id=contract_id).first()
    if contract is None:
        raise ValueError("Wartungsvertrag nicht gefunden.")
    if contract.status != "AKTIV":
        raise ValueError("Nur aktive Wartungsverträge können ausgelöst werden.")
    # Ohne offene Fälligkeit gibt es nichts auszulösen (v. a. FESTES_DATUM nach
    # der einmaligen Fälligkeit) — sonst entstünden Events mit due_date NULL.
    if contract.next_due_date is None:
        raise ValueError("Der Vertrag hat keine offene Fälligkeit.")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            result_type = result_id = None
            if contract.due_action == "AUFGABE":
                task = aufgabe_service.create_task(
                    actor_app_user_id,
                    title=f"Wartung fällig: {contract.name}",
                    due_date=contract.next_due_date,
                    project_id=contract.project_id,
                    party_id=contract.party_id,
                )
                result_type, result_id = "workflow.task", task.id
            event = MaintenanceEvent.objects.create(
                id=uuid.uuid4(),
                contract_id=contract.id,
                due_date=contract.next_due_date,
                action=contract.due_action,
                result_object_type=result_type,
                result_object_id=result_id,
                note=note,
                triggered_by_id=actor_app_user_id,
            )
            MaintenanceContract.objects.filter(id=contract_id).update(
                next_due_date=_advance_due(contract)
            )
    contract.refresh_from_db()
    return event, contract
