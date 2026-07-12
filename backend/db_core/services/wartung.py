"""Wartungs-Service: maintenance.maintenance_contract anlegen, Status wechseln
und Fälligkeits-Aktionen auslösen.

Wie die übrigen Services laufen alle Writes über business_transaction. Die
Vertragsnummer (W-…) vergibt die DB (db_default → refresh_from_db). Der
Statusautomat (AKTIV ↔ INAKTIV, INAKTIV → ARCHIVIERT) wird hier vorab geprüft
(klarer ValueError → 422) und von einem maintenance-eigenen Trigger physisch
erzwungen; Trigger-Verstöße übersetzt as_business_error in 422.

Fälligkeits-Aktion: trigger_action protokolliert die Auslösung append-only in
maintenance.maintenance_event und rückt next_due_date vor. Je nach due_action
entsteht ein echtes Folgeobjekt, auf das der Event verweist:
- AUFGABE → workflow.task
- PROJEKT → workflow.project (an der Liegenschaft des Vertrags)
- AUFTRAG → workflow.work_order (ENTWURF, an der Liegenschaft, ggf. am Projekt)
- BENACHRICHTIGUNG → kein Folgeobjekt; es gibt kein Notification-Schema im
  Backend, der MaintenanceEvent selbst IST der In-System-Vermerk.

Der (per Cron täglich aufgerufene) Fälligkeits-Scheduler ist das Management-
Command wartung_faellige_ausloesen; es ruft für jeden fälligen Vertrag genau
diese trigger_action auf.
"""
import calendar
import uuid
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import MaintenanceContract, MaintenanceEvent, Project, Property
from db_core.services import aufgabe as aufgabe_service
from db_core.services import auftrag as auftrag_service
from db_core.services import projekt as projekt_service
from db_core.services._validation import ensure_exists, ensure_party_usable

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


def _advance_due_from(base, contract):
    """Nächste Fälligkeit ausgehend von einem konkreten Datum (None = einmalig)."""
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


def _advance_due(contract):
    """Nächste Fälligkeit nach einer ausgelösten Aktion (None = einmalig, fertig)."""
    base = contract.next_due_date or contract.start_date
    return _advance_due_from(base, contract)


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
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_party_usable(party_id, "Kunde")
    ensure_exists(Project, project_id, "Projekt")

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


def _follow_up_label(contract):
    """Sprechender Name/Titel für ein automatisch erzeugtes Folgeobjekt."""
    return f"Wartung fällig: {contract.name} ({contract.next_due_date:%d.%m.%Y})"


def _create_follow_up(actor_app_user_id, contract):
    """Erzeugt das zur due_action passende echte Folgeobjekt über die vorhandenen
    Services und gibt (result_object_type, result_object_id) für den Event zurück.

    - AUFGABE  → workflow.task (an Projekt/Kunde des Vertrags)
    - PROJEKT  → workflow.project (an der Liegenschaft des Vertrags)
    - AUFTRAG  → workflow.work_order im Startstatus ENTWURF (an der Liegenschaft,
                 ggf. am Projekt des Vertrags). ENTWURF ist der einzige gültige
                 Startstatus (Trigger); die Freigabe-/Abrechnungstore greifen erst
                 bei späteren Statuswechseln — hier ist keine fachliche Auswahl
                 nötig, der Auftrag wird als Entwurf zum Weiterbearbeiten angelegt.
    - BENACHRICHTIGUNG → (None, None): es gibt kein Notification-Schema im Backend;
                 der MaintenanceEvent selbst ist der In-System-Vermerk.
    """
    action = contract.due_action
    if action == "AUFGABE":
        task = aufgabe_service.create_task(
            actor_app_user_id,
            title=_follow_up_label(contract),
            due_date=contract.next_due_date,
            project_id=contract.project_id,
            party_id=contract.party_id,
        )
        return "workflow.task", task.id
    if action == "PROJEKT":
        project = projekt_service.create_project(
            actor_app_user_id,
            name=_follow_up_label(contract),
            property_ids=[contract.property_id],
        )
        return "workflow.project", project.id
    if action == "AUFTRAG":
        order = auftrag_service.create_work_order(
            actor_app_user_id,
            property_id=contract.property_id,
            title=_follow_up_label(contract),
            project_id=contract.project_id,
            description=(
                f"Automatisch aus Wartungsvertrag {contract.contract_number} "
                f"erzeugt (Fälligkeit {contract.next_due_date:%d.%m.%Y})."
            ),
        )
        return "workflow.work_order", order.id
    return None, None


def trigger_action(actor_app_user_id, *, contract_id, note=None, catch_up_until=None):
    """Löst die Fälligkeits-Aktion des Vertrags aus.

    Protokolliert die Auslösung append-only, erzeugt je nach due_action ein echtes
    Folgeobjekt (siehe _create_follow_up) und rückt next_due_date vor. Nur auf
    aktiven Verträgen mit offener Fälligkeit möglich.

    catch_up_until (Scheduler): Liegt die Fälligkeit mehrere Intervalle in der
    Vergangenheit, wird der Vertrag TROTZDEM nur EINMAL ausgelöst (ein Event, kein
    Nachhol-Sturm), der Fälligkeitsplan aber bis über den Stichtag hinaus auf den
    nächsten künftigen Termin vorgerückt. Dadurch ist der Vertrag nach dem Lauf
    nicht mehr fällig — ein zweiter Lauf am selben Stichtag löst ihn nicht erneut
    aus (Idempotenz). None (Default, manuelle Auslösung) rückt genau ein Intervall
    vor — das bisherige Verhalten.
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

    new_due = _advance_due(contract)
    # Verpasste Intervalle überspringen: bis über den Stichtag hinaus vorrücken,
    # ohne weitere Events zu erzeugen (einmalige Auslösung je Lauf).
    if catch_up_until is not None:
        while new_due is not None and new_due <= catch_up_until:
            new_due = _advance_due_from(new_due, contract)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            result_type, result_id = _create_follow_up(actor_app_user_id, contract)
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
            _schliesse_faelligkeit(actor_app_user_id, contract, event)
            MaintenanceContract.objects.filter(id=contract_id).update(
                next_due_date=new_due
            )
    contract.refresh_from_db()
    return event, contract


def _schliesse_faelligkeit(actor_app_user_id, contract, event):
    """Hält die Fälligkeiten-Ansicht (Migration 0071) mit der Auslösung synchron.

    Es darf nur EINE Wahrheit geben: egal ob die Wartung automatisch ausgelöst
    (Command wartung_faellige_ausloesen), von Hand am Vertrag ausgelöst oder aus
    der Fälligkeiten-Ansicht erledigt wird — es entsteht genau ein
    maintenance.due_item mit genau einem Folgeobjekt.

    * Gibt es zu dieser (Vertrag, Fälligkeit) schon einen OFFENEN Eintrag, wird er
      ERLEDIGT und zeigt auf das Folgeobjekt.
    * Gibt es keinen (Fälligkeit ohne Vorlauf, Scheduler lief nie), wird der
      Nachweis nachgeholt — direkt als ERLEDIGT. Der UNIQUE-Index sorgt dafür,
      dass daraus keine Dublette wird.
    * Ist er bereits abgeschlossen (VERWORFEN/ERLEDIGT), bleibt er, wie er ist:
      eine abgeschlossene Fälligkeit wird nicht nachträglich umgeschrieben.

    Läuft INNERHALB der business_transaction von trigger_action.
    """
    # Lokaler Import: faelligkeit importiert wartung nicht, aber die Modul-Kette
    # (faelligkeit → einsatz/beleg/…) soll beim Import von wartung nicht mitziehen.
    from db_core.models import DueItem

    felder = dict(
        status="ERLEDIGT",
        result_object_type=event.result_object_type,
        result_object_id=event.result_object_id,
        resolution_note=event.note,
        resolved_at=timezone.now(),
        resolved_by_id=actor_app_user_id,
    )
    geaendert = DueItem.objects.filter(
        contract_id=contract.id, due_date=contract.next_due_date, status="OFFEN"
    ).update(**felder)
    if geaendert:
        return
    if DueItem.objects.filter(
        contract_id=contract.id, due_date=contract.next_due_date
    ).exists():
        return  # bereits abgeschlossen — nicht umschreiben
    try:
        with transaction.atomic():
            DueItem.objects.create(
                id=uuid.uuid4(),
                kind="WARTUNG",
                contract_id=contract.id,
                property_id=contract.property_id,
                title=f"Wartung: {contract.name}",
                due_date=contract.next_due_date,
                lead_time_days=contract.lead_time_days or 0,
                created_by_id=actor_app_user_id,
                **felder,
            )
    except IntegrityError:
        # Paralleler Lauf war schneller — der UNIQUE-Index hat gewonnen. Kein
        # Problem: der Nachweis existiert.
        pass
