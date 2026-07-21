"""Personal-Service: hr.employee, hr.employment_contract, hr.absence,
hr.vacation_budget (Migration 0019).

Wie die übrigen Services laufen alle Writes über business_transaction; die
Personalnummer (MA-…) vergibt die DB (db_default → refresh_from_db).

Zwei Regeln tragen die Domäne und werden hier vorab geprüft (klarer ValueError
→ 422), von der DB aber zusätzlich physisch erzwungen:

* **Verträge überlappen nicht.** Ein Mitarbeiter hat zu jedem Zeitpunkt höchstens
  einen Vertrag (EXCLUDE-Constraint). Ein Folgevertrag schließt den laufenden
  automatisch am Vortag ab, statt den Anwender in den Constraint laufen zu lassen.
* **Kein rückwirkendes Überschreiben.** Beginn, Sollstunden, Urlaubsanspruch und
  Lohngruppe eines bestehenden Vertrags sind unveränderlich (Trigger); eine
  Arbeitszeitänderung erzeugt einen neuen Vertrag.

Die angerechneten Abwesenheitstage (days_count) berechnet der Service aus dem
Sollstunden-Raster des jeweils gültigen Vertrags — Tage ohne Soll (Wochenende,
0-Stunden-Tage) zählen nicht. Der Client liefert days_count nie selbst.

Bekannte Lücke (bewusst): ein Feiertagskalender existiert im Schema nicht, daher
zählen gesetzliche Feiertage derzeit als Arbeitstage, wenn der Vertrag für den
Wochentag ein Soll ausweist. Ebenso ist der unterjährige Vertragsbeginn nicht
automatisch in den Urlaubsanspruch eingerechnet (Hero verhält sich genauso; die
manuelle Anpassung am Urlaubskonto ist dafür vorgesehen).
"""
import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from db_core.db_context import business_transaction, run_business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Absence,
    AppUser,
    CompanyProfile,
    Employee,
    EmployeeTrade,
    EmploymentContract,
    Person,
    Trade,
    VacationBudget,
    WageGroup,
)
from db_core.services._validation import ensure_exists
from db_core.services import identity as identity_service
from db_core.services.identity import personenname

EMPLOYEE_STATUS = ("AKTIV", "INAKTIV", "AUSGETRETEN")
ABSENCE_TYPES = (
    "URLAUB",
    "KRANKHEIT",
    "ELTERNZEIT",
    "SONDERURLAUB",
    "UNBEZAHLT",
    "FORTBILDUNG",
)

# Erlaubte Statusübergänge → {Zielstatus}. AUSGETRETEN ist final; ein
# Wiedereintritt ist fachlich ein neuer Personalsatz. Spiegelt den DB-Trigger
# hr.enforce_employee_status.
EMPLOYEE_TRANSITIONS = {
    "AKTIV": {"INAKTIV", "AUSGETRETEN"},
    "INAKTIV": {"AKTIV", "AUSGETRETEN"},
    "AUSGETRETEN": set(),
}

# Spiegelt hr.enforce_absence_status.
ABSENCE_TRANSITIONS = {
    "ENTWURF": {"EINGEREICHT", "ZURUECKGEZOGEN"},
    "EINGEREICHT": {"GENEHMIGT", "ABGELEHNT", "ZURUECKGEZOGEN"},
    "GENEHMIGT": set(),
    "ABGELEHNT": set(),
    "ZURUECKGEZOGEN": set(),
}

# Wochentag (date.weekday(): Mo=0) → Spalte des Sollstunden-Rasters.
_WEEKDAY_FIELDS = (
    "hours_monday",
    "hours_tuesday",
    "hours_wednesday",
    "hours_thursday",
    "hours_friday",
    "hours_saturday",
    "hours_sunday",
)

_HALF = Decimal("0.5")
_ONE = Decimal("1")


def _get_employee(employee_id):
    employee = Employee.objects.filter(id=employee_id).first()
    if employee is None:
        raise ValueError(f"Mitarbeiter {employee_id} existiert nicht")
    return employee


def list_employee_trades(employee_id, *, include_inactive=False):
    """Die Gewerke eines Mitarbeiters, in Katalogreihenfolge."""
    qs = EmployeeTrade.objects.filter(employee_id=employee_id)
    if not include_inactive:
        qs = qs.filter(active=True)
    return list(
        qs.select_related("trade").order_by(
            "trade__sort_order", "trade__label", "trade__id"
        )
    )


def set_employee_trades(actor_app_user_id, *, employee_id, trade_ids):
    """Setzt die Gewerke eines Mitarbeiters auf genau diese Menge (Vollersetzung).

    Warum Vollersetzung statt Einzeloperationen: Die Oberfläche zeigt eine Liste
    mit Häkchen; „was angehakt ist, gilt" ist genau diese Semantik. Zwei getrennte
    Aufrufe (hinzufügen/entfernen) wären für den Aufrufer nur eine Fehlerquelle.

    hr.employee_trade verbietet DELETE (Schutzstandard). Was wegfällt, wird
    deshalb **deaktiviert**, nicht gelöscht — und was zurückkommt, reaktiviert
    die vorhandene Zeile, statt eine zweite anzulegen (der UNIQUE-Schlüssel
    ließe das ohnehin nicht zu).
    """
    _get_employee(employee_id)
    gewuenscht = set(trade_ids or ())
    if gewuenscht:
        vorhanden = set(
            Trade.objects.filter(id__in=gewuenscht).values_list("id", flat=True)
        )
        fehlend = gewuenscht - vorhanden
        if fehlend:
            raise ValueError(f"Unbekanntes Gewerk: {sorted(str(f) for f in fehlend)}")

    with business_transaction(actor_app_user_id):
        bekannt = dict(
            EmployeeTrade.objects.filter(employee_id=employee_id).values_list(
                "trade_id", "id"
            )
        )
        for trade_id in gewuenscht - set(bekannt):
            EmployeeTrade.objects.create(
                id=uuid.uuid4(), employee_id=employee_id, trade_id=trade_id
            )
        # Vorhandene Zeilen nur dort anfassen, wo sich der Zustand ändert — sonst
        # schriebe jeder Speichervorgang sinnlose Audit-Einträge.
        EmployeeTrade.objects.filter(
            employee_id=employee_id, trade_id__in=gewuenscht & set(bekannt), active=False
        ).update(active=True)
        EmployeeTrade.objects.filter(
            employee_id=employee_id, active=True
        ).exclude(trade_id__in=gewuenscht).update(active=False)
    return list_employee_trades(employee_id)


def _contract_on(contracts, day):
    """Der an einem Tag gültige Vertrag aus einer vorgeladenen Liste (oder None).

    Überlappungsfreiheit garantiert der EXCLUDE-Constraint, daher ist das
    Ergebnis eindeutig.
    """
    for contract in contracts:
        if contract.valid_from <= day and (
            contract.valid_to is None or contract.valid_to >= day
        ):
            return contract
    return None


def _is_working_day(contract, day):
    """Weist der Vertrag für den Wochentag dieses Tages ein Soll > 0 aus?"""
    if contract is None:
        return False
    hours = getattr(contract, _WEEKDAY_FIELDS[day.weekday()])
    return hours > 0


def compute_absence_days(employee_id, start_date, end_date, half_day_start, half_day_end):
    """Angerechnete Arbeitstage im Zeitraum, halbe Randtage berücksichtigt.

    Tage ohne gültigen Vertrag oder mit Soll 0 zählen nicht mit. Ein halber
    Randtag zieht 0,5 ab — aber nur, wenn dieser Randtag überhaupt ein
    Arbeitstag ist.
    """
    # Die im Zeitraum überhaupt relevanten Verträge einmal laden (statt je Tag).
    contracts = list(
        EmploymentContract.objects.filter(
            employee_id=employee_id, valid_from__lte=end_date
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=start_date))
    )

    days = Decimal("0")
    day = start_date
    while day <= end_date:
        if _is_working_day(_contract_on(contracts, day), day):
            days += _ONE
        day += timedelta(days=1)

    if days == 0:
        return days

    if half_day_start and _is_working_day(_contract_on(contracts, start_date), start_date):
        days -= _HALF
    if half_day_end and _is_working_day(_contract_on(contracts, end_date), end_date):
        days -= _HALF
    return days


# --- Personalsatz ---------------------------------------------------------


def create_employee(
    actor_app_user_id,
    *,
    app_user_id,
    hired_on,
    party_id=None,
    first_name=None,
    last_name=None,
    salutation=None,
    birth_date=None,
    wage_group_id=None,
    notes=None,
):
    """Legt einen Personalsatz an — Personendaten **direkt**, nicht ausgewählt.

    Befund F1. Sascha wörtlich: „Warum kann ich Personen aus meinen Kontakten
    da finden? Ich lege meine Mitarbeiter ja nicht wie einen Kunden an!"

    Das Datenmodell ist nicht das Problem: `hr.employee` ist ein eigenes Schema
    mit Personalnummer, Verträgen und Abwesenheiten; die Party trägt für
    Mitarbeiter faktisch nur Vor- und Nachname. Lohn läuft über die Lohngruppe,
    Zeiterfassung über das Login-Konto — **nicht** über die Party.

    Das Problem war der Weg dorthin: Wer einen Mitarbeiter anlegen wollte,
    musste ihn **vorher als Kontakt im Kundenstamm** erfassen und fand ihn dann
    in derselben Trefferliste wie die Kundschaft. Datenschutzrechtlich ist das
    die falsche Richtung — Beschäftigten- und Kundendaten haben verschiedene
    Rechtsgrundlagen, Zwecke und Löschfristen.

    Deshalb: Wer `first_name`/`last_name` schickt, bekommt die `identity.person`
    **im Hintergrund** angelegt; den Kontakt-Picker sieht niemand mehr. Wer
    stattdessen `party_id` schickt, verknüpft eine bestehende Person — der
    Monteur, der zugleich Kunde ist, bleibt damit möglich. Dasselbe Verhalten
    hat Odoo (`hr.employee.work_contact_id` ist dort ausdrücklich optional).

    Alles in EINER Transaktion: Scheitert der Personalsatz, bleibt keine
    Person-Waise zurück, die der No-Delete-Schutz nicht mehr entfernen könnte.
    """
    ensure_exists(AppUser, app_user_id, "Benutzerkonto")
    ensure_exists(WageGroup, wage_group_id, "Lohngruppe")
    if party_id is None and not (last_name and last_name.strip()):
        raise ValueError(
            "Für einen neuen Mitarbeiter ist der Nachname Pflicht — oder wähle "
            "eine bestehende Person."
        )
    if party_id is not None:
        ensure_exists(Person, party_id, "Person")
    if Employee.objects.filter(app_user_id=app_user_id).exists():
        raise ValueError("Für dieses Benutzerkonto existiert bereits ein Personalsatz")
    if party_id is not None and Employee.objects.filter(party_id=party_id).exists():
        raise ValueError("Für diese Person existiert bereits ein Personalsatz")

    def _anlegen():
        ziel_party = party_id
        if ziel_party is None:
            # Die Person entsteht im Hintergrund — der Anwender sieht keinen
            # Kontakt-Picker. `create_person` macht den Vornamen seit 0125
            # optional und wirft bei leerem Nachnamen.
            person = identity_service.create_person(
                actor_app_user_id,
                first_name,
                last_name,
                salutation=salutation,
                birth_date=birth_date,
            )
            ziel_party = person.id
        return Employee.objects.create(
            id=uuid.uuid4(),
            app_user_id=app_user_id,
            party_id=ziel_party,
            wage_group_id=wage_group_id,
            status="AKTIV",
            hired_on=hired_on,
            notes=notes,
            created_by_id=actor_app_user_id,
        )

    # EINE Klammer über Person und Personalsatz: Scheitert der Personalsatz,
    # bleibt keine Person-Waise zurück, die der No-Delete-Schutz nicht mehr
    # entfernen könnte. Die service-internen `business_transaction`-Aufrufe
    # werden dabei zu Savepoints.
    with as_business_error():
        employee = run_business_transaction(actor_app_user_id, _anlegen)
    employee.refresh_from_db()
    return employee


def set_employee_status(actor_app_user_id, *, employee_id, status, left_on=None):
    """Statuswechsel. AUSGETRETEN verlangt ein Austrittsdatum (DB-CHECK)."""
    employee = _get_employee(employee_id)
    if status not in EMPLOYEE_STATUS:
        raise ValueError(f"Unbekannter Status: {status}")
    if status not in EMPLOYEE_TRANSITIONS[employee.status]:
        raise ValueError(
            f"Statuswechsel {employee.status} -> {status} ist nicht zulässig"
        )
    if status == "AUSGETRETEN":
        if left_on is None:
            raise ValueError("Austritt erfordert ein Austrittsdatum")
        if left_on < employee.hired_on:
            raise ValueError("Das Austrittsdatum liegt vor dem Eintrittsdatum")
    elif left_on is not None:
        raise ValueError("Ein Austrittsdatum ist nur beim Austritt zulässig")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            Employee.objects.filter(id=employee_id).update(
                status=status, left_on=left_on
            )
    employee.refresh_from_db()
    return employee


# --- Arbeitsvertrag -------------------------------------------------------


def create_contract(
    actor_app_user_id,
    *,
    employee_id,
    valid_from,
    hours,
    vacation_days_per_year,
    valid_to=None,
    wage_group_id=None,
    notes=None,
):
    """Legt einen Vertrag an; `hours` ist ein Dict Wochentag-Feld → Stunden.

    Ein noch laufender Vorgängervertrag wird am Vortag des neuen Beginns
    beendet (Hero: Arbeitszeitänderung = neuer Vertrag). Rückwirkende Verträge,
    die einen bereits beendeten Zeitraum überlappen, lehnt der Service ab.
    """
    employee = _get_employee(employee_id)
    ensure_exists(WageGroup, wage_group_id, "Lohngruppe")
    if employee.status == "AUSGETRETEN":
        raise ValueError("Für einen ausgetretenen Mitarbeiter kann kein Vertrag angelegt werden")

    unknown = set(hours) - set(_WEEKDAY_FIELDS)
    if unknown:
        raise ValueError(f"Unbekannte Sollstunden-Felder: {sorted(unknown)}")

    values = {field: Decimal(str(hours.get(field, 0))) for field in _WEEKDAY_FIELDS}
    for field, value in values.items():
        if value < 0 or value > 24:
            raise ValueError(f"{field}: Sollstunden müssen zwischen 0 und 24 liegen")
    if sum(values.values()) <= 0:
        raise ValueError("Der Vertrag muss mindestens einen Arbeitstag ausweisen")

    vacation_days = Decimal(str(vacation_days_per_year))
    if vacation_days < 0 or vacation_days > 366:
        raise ValueError("Urlaubsanspruch muss zwischen 0 und 366 Tagen liegen")
    if valid_to is not None and valid_to < valid_from:
        raise ValueError("Das Vertragsende liegt vor dem Vertragsbeginn")
    if valid_from < employee.hired_on:
        raise ValueError("Der Vertragsbeginn liegt vor dem Eintrittsdatum")

    # Vorgänger, der in den neuen Zeitraum hineinragt.
    predecessor = (
        EmploymentContract.objects.filter(employee_id=employee_id, valid_from__lt=valid_from)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=valid_from))
        .order_by("-valid_from")
        .first()
    )
    # Verträge, die am oder nach dem neuen Beginn liegen, blockieren.
    if EmploymentContract.objects.filter(
        employee_id=employee_id, valid_from__gte=valid_from
    ).exists():
        raise ValueError(
            "Es existiert bereits ein Vertrag ab diesem Datum oder später; "
            "rückwirkende Verträge sind nicht zulässig"
        )

    # Einen bereits gekündigten Vertrag still zu kürzen wäre eine rückwirkende
    # Änderung an einer abgeschlossenen Vereinbarung — das lehnen wir ab.
    if predecessor is not None and predecessor.status == "GEKUENDIGT":
        raise ValueError(
            f"Der Vorgängervertrag ist zum {predecessor.valid_to} gekündigt; "
            "der neue Vertrag darf frühestens am Folgetag beginnen"
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            if predecessor is not None:
                EmploymentContract.objects.filter(id=predecessor.id).update(
                    valid_to=valid_from - timedelta(days=1)
                )
            contract = EmploymentContract.objects.create(
                id=uuid.uuid4(),
                employee_id=employee_id,
                valid_from=valid_from,
                valid_to=valid_to,
                vacation_days_per_year=vacation_days,
                wage_group_id=wage_group_id,
                status="AKTIV",
                notes=notes,
                created_by_id=actor_app_user_id,
                **values,
            )
    contract.refresh_from_db()
    return contract


def terminate_contract(actor_app_user_id, *, contract_id, valid_to, reason):
    """Kündigt einen Vertrag zum Datum `valid_to` (begründungspflichtig)."""
    contract = EmploymentContract.objects.filter(id=contract_id).first()
    if contract is None:
        raise ValueError(f"Vertrag {contract_id} existiert nicht")
    if contract.status == "GEKUENDIGT":
        raise ValueError("Der Vertrag ist bereits gekündigt")
    if not (reason or "").strip():
        raise ValueError("Eine Kündigung ist begründungspflichtig")
    if valid_to < contract.valid_from:
        raise ValueError("Das Vertragsende liegt vor dem Vertragsbeginn")

    # Ein Ende hinter dem Beginn des Folgevertrags würde den EXCLUDE-Constraint
    # verletzen (IntegrityError → 500). Vorab prüfen, damit daraus ein 422 wird.
    successor = (
        EmploymentContract.objects.filter(
            employee_id=contract.employee_id, valid_from__gt=contract.valid_from
        )
        .order_by("valid_from")
        .first()
    )
    if successor is not None and valid_to >= successor.valid_from:
        raise ValueError(
            f"Ein Folgevertrag beginnt am {successor.valid_from}; das Vertragsende "
            "muss davor liegen"
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            EmploymentContract.objects.filter(id=contract_id).update(
                status="GEKUENDIGT", valid_to=valid_to, termination_reason=reason.strip()
            )
    contract.refresh_from_db()
    return contract


# --- Abwesenheit ----------------------------------------------------------


def create_absence(
    actor_app_user_id,
    *,
    employee_id,
    absence_type,
    start_date,
    end_date,
    half_day_start=False,
    half_day_end=False,
    reason=None,
):
    """Legt einen Abwesenheitsantrag im Status ENTWURF an (Trigger erzwingt das).

    days_count wird berechnet, nicht übergeben.
    """
    _get_employee(employee_id)
    if absence_type not in ABSENCE_TYPES:
        raise ValueError(f"Unbekannte Abwesenheitsart: {absence_type}")
    if end_date < start_date:
        raise ValueError("Das Enddatum liegt vor dem Startdatum")
    if start_date == end_date and half_day_end:
        raise ValueError(
            "Ein eintägiger Zeitraum kann nur eine Hälfte sein — halber Starttag genügt"
        )

    days = compute_absence_days(
        employee_id, start_date, end_date, half_day_start, half_day_end
    )
    if days <= 0:
        raise ValueError(
            "Der Zeitraum enthält keinen Arbeitstag laut gültigem Vertrag "
            "(fehlender Vertrag, Wochenende oder 0-Stunden-Tage)"
        )

    # Ein halber Randtag, der gar kein Arbeitstag ist, bleibt wirkungslos —
    # dann darf das Flag auch nicht gespeichert werden, sonst zeigt das UI ein
    # „½" ohne Entsprechung in days_count.
    contracts = list(
        EmploymentContract.objects.filter(
            employee_id=employee_id, valid_from__lte=end_date
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=start_date))
    )
    half_day_start = half_day_start and _is_working_day(
        _contract_on(contracts, start_date), start_date
    )
    half_day_end = half_day_end and _is_working_day(
        _contract_on(contracts, end_date), end_date
    )

    overlapping = Absence.objects.filter(
        employee_id=employee_id,
        status__in=("ENTWURF", "EINGEREICHT", "GENEHMIGT"),
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).exists()
    if overlapping:
        raise ValueError("Für diesen Zeitraum existiert bereits eine Abwesenheit")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            absence = Absence.objects.create(
                id=uuid.uuid4(),
                employee_id=employee_id,
                absence_type=absence_type,
                start_date=start_date,
                end_date=end_date,
                half_day_start=half_day_start,
                half_day_end=half_day_end,
                days_count=days,
                status="ENTWURF",
                reason=reason,
                created_by_id=actor_app_user_id,
            )
    absence.refresh_from_db()
    return absence


def _advance_absence(actor_app_user_id, absence_id, target, **fields):
    absence = Absence.objects.filter(id=absence_id).first()
    if absence is None:
        raise ValueError(f"Abwesenheit {absence_id} existiert nicht")
    if target not in ABSENCE_TRANSITIONS[absence.status]:
        raise ValueError(
            f"Statuswechsel {absence.status} -> {target} ist nicht zulässig"
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            Absence.objects.filter(id=absence_id).update(status=target, **fields)
    absence.refresh_from_db()
    return absence


def submit_absence(actor_app_user_id, *, absence_id):
    return _advance_absence(actor_app_user_id, absence_id, "EINGEREICHT")


def withdraw_absence(actor_app_user_id, *, absence_id):
    return _advance_absence(actor_app_user_id, absence_id, "ZURUECKGEZOGEN")


def approve_absence(actor_app_user_id, *, absence_id, note=None):
    """Genehmigt einen eingereichten Antrag. Der Entscheider wird protokolliert."""
    from django.utils import timezone

    return _advance_absence(
        actor_app_user_id,
        absence_id,
        "GENEHMIGT",
        decided_by_id=actor_app_user_id,
        decided_at=timezone.now(),
        decision_note=note,
    )


def reject_absence(actor_app_user_id, *, absence_id, note):
    """Lehnt einen eingereichten Antrag ab — begründungspflichtig (DB-CHECK)."""
    from django.utils import timezone

    if not (note or "").strip():
        raise ValueError("Eine Ablehnung ist begründungspflichtig")
    return _advance_absence(
        actor_app_user_id,
        absence_id,
        "ABGELEHNT",
        decided_by_id=actor_app_user_id,
        decided_at=timezone.now(),
        decision_note=note.strip(),
    )


# --- Urlaubskonto ---------------------------------------------------------


def set_vacation_budget(
    actor_app_user_id,
    *,
    employee_id,
    year,
    entitlement_days,
    carryover_days=0,
    adjustment_days=0,
    adjustment_reason=None,
):
    """Legt das Urlaubskonto eines Jahres an oder aktualisiert es (idempotent)."""
    _get_employee(employee_id)
    if not 2000 <= year <= 2100:
        raise ValueError("Jahr muss zwischen 2000 und 2100 liegen")

    entitlement = Decimal(str(entitlement_days))
    carryover = Decimal(str(carryover_days))
    adjustment = Decimal(str(adjustment_days))
    if entitlement < 0:
        raise ValueError("Der Urlaubsanspruch darf nicht negativ sein")
    if carryover < 0:
        raise ValueError("Der Übertrag darf nicht negativ sein")
    if adjustment != 0 and not (adjustment_reason or "").strip():
        raise ValueError("Eine Anpassung des Urlaubskontos ist begründungspflichtig")

    reason = (adjustment_reason or "").strip() or None
    existing = VacationBudget.objects.filter(employee_id=employee_id, year=year).first()

    with as_business_error():
        with business_transaction(actor_app_user_id):
            if existing is None:
                budget = VacationBudget.objects.create(
                    id=uuid.uuid4(),
                    employee_id=employee_id,
                    year=year,
                    entitlement_days=entitlement,
                    carryover_days=carryover,
                    adjustment_days=adjustment,
                    adjustment_reason=reason,
                    created_by_id=actor_app_user_id,
                )
            else:
                VacationBudget.objects.filter(id=existing.id).update(
                    entitlement_days=entitlement,
                    carryover_days=carryover,
                    adjustment_days=adjustment,
                    adjustment_reason=reason,
                )
                budget = existing
    budget.refresh_from_db()
    return budget


def carryover_expiry_date(year, profile=None):
    """Verfallstag des Übertrags IM Jahr `year` — oder None („kein Verfall").

    Die Regel ist eine **Firmeneinstellung** (`company.company_profile`,
    Migration 0072) und steht per Default auf NULL: **Ohne ausdrückliche
    Einstellung verfällt nichts.** Das ist bewusst so herum:

    * § 7 Abs. 3 BUrlG *erlaubt* die Übertragung mit Verfall zum 31.03., er
      ordnet sie nicht an — sie setzt betriebliche oder personenbezogene Gründe
      voraus und ist Sache der Vereinbarung.
    * Nach BAG/EuGH verfällt der Urlaub ohnehin nur, wenn der Arbeitgeber den
      Beschäftigten rechtzeitig aufgefordert und belehrt hat.

    Eine Software, die von sich aus Ansprüche wegrechnet, träfe damit eine
    rechtliche Entscheidung, die ihr nicht zusteht. Also: erst einstellen, dann
    rechnen.

    Ein 29./30./31. in einem zu kurzen Monat wird auf den Monatsletzten gekappt
    (der Betrieb meint „Monatsende", nicht „gar nicht").
    """
    profile = profile if profile is not None else CompanyProfile.objects.first()
    if profile is None:
        return None
    month = profile.vacation_carryover_expiry_month
    day = profile.vacation_carryover_expiry_day
    if not month or not day:
        return None
    letzter = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, letzter))


def _verbrauch_bis(absence, stichtag):
    """Die Urlaubstage einer Abwesenheit, die **bis zum Stichtag** liegen.

    Review-Befund A3: Vorher wurde `days_count` VOLLSTÄNDIG dem Zeitraum vor dem
    Verfallstag zugerechnet, sobald der Urlaub davor begann. Ein Urlaub vom
    30.03. bis 10.04. hätte damit 12 Tage „bis zum 31.03." verbraucht — es
    verfiele zu wenig. Der Fehler wirkte zugunsten des Beschäftigten, war aber
    trotzdem eine falsche Zahl.

    Liegt die Abwesenheit ganz vor dem Stichtag, bleibt es bei `days_count` (kein
    Neurechnen — der gespeicherte Wert ist die Wahrheit). Ragt sie darüber
    hinaus, wird der Teil bis zum Stichtag mit derselben Funktion gerechnet, die
    ihn ursprünglich ermittelt hat (`compute_absence_days`, Sollstunden-Raster) —
    der halbe **End**tag zählt dabei nicht mehr, denn er liegt jenseits des
    Stichtags.
    """
    if absence.end_date <= stichtag:
        return absence.days_count
    return compute_absence_days(
        absence.employee_id,
        absence.start_date,
        stichtag,
        absence.half_day_start,
        False,
    )


def vacation_account(employee_id, year, today=None, profile=None):
    """Urlaubskonto eines Jahres inklusive abgeleitetem Verbrauch, Verfall, Rest.

    Verbrauch = Summe der days_count aller GENEHMIGTEN URLAUB-Abwesenheiten,
    die im Jahr *beginnen*. (Jahresübergreifende Anträge werden dem Startjahr
    zugerechnet — eine tagegenaue Aufteilung wäre erst mit Feiertagskalender
    sinnvoll.)

    **Verfall (nur wenn eingestellt).** Ist im Firmenprofil ein Verfallstag
    gepflegt (z. B. 31.03.), verfällt der **Übertrag** aus dem Vorjahr, soweit er
    bis dahin nicht verbraucht ist — und erst, wenn der Tag vorbei ist. Der
    Übertrag wird dabei **zuerst** verbraucht (er verfällt als Erstes; den
    laufenden Anspruch trifft der Verfall nicht). Rechnung:

        verfallen = max(0, Übertrag − Verbrauch bis zum Verfallstag)   [nur nach dem Tag]
        Rest      = Anspruch + Übertrag + Anpassung − Verbrauch − verfallen

    Ohne Einstellung ist `expired_days` immer 0 und die Rechnung exakt die
    bisherige. **Der Verbrauch bleibt abgeleitet, nichts wird gespeichert.**
    """
    budget = VacationBudget.objects.filter(employee_id=employee_id, year=year).first()
    entitlement = budget.entitlement_days if budget else Decimal("0")
    carryover = budget.carryover_days if budget else Decimal("0")
    adjustment = budget.adjustment_days if budget else Decimal("0")

    used = Decimal("0")
    used_bis_verfall = Decimal("0")
    verfallstag = carryover_expiry_date(year, profile=profile)
    absences = Absence.objects.filter(
        employee_id=employee_id,
        absence_type="URLAUB",
        status="GENEHMIGT",
        start_date__year=year,
    )
    for absence in absences:
        used += absence.days_count
        if verfallstag is not None and absence.start_date <= verfallstag:
            used_bis_verfall += _verbrauch_bis(absence, verfallstag)

    heute = today or date.today()
    expired = Decimal("0")
    if verfallstag is not None and heute > verfallstag and carryover > 0:
        expired = max(Decimal("0"), carryover - used_bis_verfall)

    total = entitlement + carryover + adjustment
    return {
        "year": year,
        "entitlement_days": entitlement,
        "carryover_days": carryover,
        "adjustment_days": adjustment,
        "adjustment_reason": budget.adjustment_reason if budget else None,
        "total_days": total,
        "used_days": used,
        "expiry_date": verfallstag,
        "expired_days": expired,
        "remaining_days": total - used - expired,
    }


# --- Resturlaubs-Übertrag ins Folgejahr -----------------------------------


def _uebertrag_kandidaten():
    """Wer bekommt einen Übertrag? Alle, die noch da sind.

    AUSGETRETENE bleiben außen vor: ihr Resturlaub wird nach § 7 Abs. 4 BUrlG
    **abgegolten** (ausgezahlt), nicht übertragen — ein Übertrag ins Folgejahr
    wäre für sie eine Falschaussage. INAKTIVE (z. B. Elternzeit) bleiben drin:
    ihr Anspruch besteht fort.
    """
    return (
        Employee.objects.exclude(status="AUSGETRETEN")
        .select_related("party")
        .order_by("party__last_name", "party__first_name")
    )


def _naechster_anspruch(employee_id, ziel_jahr):
    """Urlaubsanspruch für `ziel_jahr` aus dem am 1.1. gültigen Vertrag (oder 0)."""
    stichtag = date(ziel_jahr, 1, 1)
    contracts = list(
        EmploymentContract.objects.filter(
            employee_id=employee_id, valid_from__lte=date(ziel_jahr, 12, 31)
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=stichtag))
    )
    # Der am 1.1. gültige Vertrag; sonst der erste, der im Zieljahr beginnt.
    contract = _contract_on(contracts, stichtag)
    if contract is None and contracts:
        contract = sorted(contracts, key=lambda c: c.valid_from)[0]
    return contract.vacation_days_per_year if contract else Decimal("0")


def carryover_vorschau(year, employee_ids=None):
    """Vorschau „Resturlaub aus `year` → Übertrag in `year+1`", je Mitarbeiter.

    **Der Übertrag SETZT den Wert, er addiert nicht** (`carryover_days` des
    Folgejahres := Rest aus `year`). Genau das macht die Übernahme **idempotent**:
    Zweimal drücken schreibt zweimal denselben Wert. Ein Addieren wäre die
    typische Doppelbuchungsfalle — der Übertrag verdoppelte sich beim
    versehentlichen zweiten Klick, und niemand könnte hinterher sagen, welcher
    Wert der richtige war.

    Ein negativer Rest (Minusurlaub durch Anpassung) wird auf 0 gekappt: die DB
    verbietet einen negativen Übertrag, und „Minusurlaub ins nächste Jahr
    schleppen" ist keine Praxis, die wir stillschweigend einführen.
    """
    if not 2000 <= year <= 2099:
        raise ValueError("Jahr muss zwischen 2000 und 2099 liegen")
    ziel = year + 1
    profile = CompanyProfile.objects.first()
    qs = _uebertrag_kandidaten()
    if employee_ids:
        qs = qs.filter(id__in=list(employee_ids))

    zeilen = []
    for emp in qs:
        konto = vacation_account(emp.id, year, profile=profile)
        rest = _tage(konto["remaining_days"])
        neu = max(Decimal("0.00"), rest)
        ziel_budget = VacationBudget.objects.filter(
            employee_id=emp.id, year=ziel
        ).first()
        aktuell = _tage(
            ziel_budget.carryover_days if ziel_budget else Decimal("0")
        )
        anspruch = _tage(
            ziel_budget.entitlement_days
            if ziel_budget
            else _naechster_anspruch(emp.id, ziel)
        )
        zeilen.append(
            {
                "employee_id": emp.id,
                "employee_number": emp.employee_number,
                "name": personenname(emp.party.first_name, emp.party.last_name),
                "year": year,
                "target_year": ziel,
                "entitlement_days": _tage(konto["entitlement_days"]),
                "used_days": _tage(konto["used_days"]),
                "expired_days": _tage(konto["expired_days"]),
                "remaining_days": rest,
                "carryover_current": aktuell,
                "carryover_new": neu,
                "target_entitlement_days": anspruch,
                "changes": neu != aktuell,
            }
        )
    return zeilen


def _tage(wert):
    """Urlaubstage auf die DB-Skala quantisieren (numeric(5,2)) — damit die API
    „0.00" liefert und nicht mal „0", mal „0.00"."""
    return Decimal(wert).quantize(Decimal("0.01"))


def carryover_uebertragen(actor_app_user_id, *, year, employee_ids=None):
    """Führt den Übertrag aus (idempotent: er SETZT `carryover_days` im Folgejahr).

    Der Anspruch des Folgejahres bleibt unangetastet, wenn dort schon ein
    Urlaubskonto steht; existiert keins, wird es aus dem gültigen Vertrag
    angelegt (`vacation_days_per_year`). Eine bestehende Anpassung samt Grund
    wird unverändert mitgeschrieben — sie ist eine eigene, begründete
    Entscheidung und darf vom Übertrag nicht verschluckt werden.
    """
    zeilen = carryover_vorschau(year, employee_ids)
    ziel = year + 1
    ergebnisse = []
    # Ein Lauf, eine Transaktion (Review-Befund A4): Scheitert der Übertrag beim
    # siebten von zwanzig Mitarbeitenden, darf nicht die halbe Belegschaft
    # übertragen sein und die andere nicht. `set_vacation_budget` öffnet intern
    # seine eigene `business_transaction` — die wird hier zum Savepoint und rollt
    # mit zurück.
    with transaction.atomic():
        for zeile in zeilen:
            budget = VacationBudget.objects.filter(
                employee_id=zeile["employee_id"], year=ziel
            ).first()
            set_vacation_budget(
                actor_app_user_id,
                employee_id=zeile["employee_id"],
                year=ziel,
                entitlement_days=(
                    budget.entitlement_days
                    if budget
                    else zeile["target_entitlement_days"]
                ),
                carryover_days=zeile["carryover_new"],
                adjustment_days=budget.adjustment_days if budget else Decimal("0"),
                adjustment_reason=budget.adjustment_reason if budget else None,
            )
            # Nach dem Schreiben IST der neue Wert der aktuelle — und es steht
            # nichts mehr aus (`changes=False`). Sonst zeigte die Tabelle
            # „30 → 30" und der Knopf böte einen Übertrag an, der schon
            # geschrieben ist.
            ergebnisse.append(
                {**zeile, "carryover_current": zeile["carryover_new"], "changes": False}
            )
    return ergebnisse


# --- Abwesenheiten: Übersicht und Export ----------------------------------


def abwesenheits_zeilen(von, bis, employee_id=None, status=None):
    """Zeilen für den Abwesenheits-CSV-Export — **mit** Abwesenheitsart.

    DSGVO Art. 9: Die Art unterscheidet Urlaub von Krankheit und ist damit ein
    Gesundheitsdatum. Dieser Export gehört deshalb hinter das `hr`-Tor (siehe
    `api/mitarbeiter.py`) und ausdrücklich NICHT in die Planungssicht — die
    bekommt „abwesend, von–bis" ohne Art (`services/planung.py`).
    """
    qs = (
        Absence.objects.select_related("employee", "employee__party", "decided_by")
        .filter(start_date__lte=bis, end_date__gte=von)
        .order_by("start_date", "employee__party__last_name")
    )
    if employee_id is not None:
        qs = qs.filter(employee_id=employee_id)
    if status:
        qs = qs.filter(status=status)
    return [
        {
            "employee_number": a.employee.employee_number,
            "name": personenname(a.employee.party.first_name, a.employee.party.last_name),
            "absence_type": a.absence_type,
            "start_date": a.start_date,
            "end_date": a.end_date,
            "half_day_start": a.half_day_start,
            "half_day_end": a.half_day_end,
            "days_count": a.days_count,
            "status": a.status,
            "reason": a.reason or "",
            "decided_by": a.decided_by.display_name if a.decided_by_id else "",
        }
        for a in qs
    ]
